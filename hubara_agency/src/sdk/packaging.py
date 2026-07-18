"""Empaquetador/instalador de plugins — formato ``acktospkg/1`` (Acktos Packages).

Qué resuelve: mover un plugin entre repos Hubara-shaped (el central y los
clones forjados por ``forge``) sin tocar NINGÚN archivo central — posible
porque INV-1 garantiza que un plugin vive completo bajo sus 4 paths
single-owner (backend, frontend+manifest, tests de dominio, TCK instanciado)
y todo lo compartido se REGENERA (plugins:sync, render-compose).

Formato del paquete (tar.gz)::

    <name>.acktospkg
    ├── package.yaml          # format + name + source + índice de unidades
    ├── units/plugin-<id>/
    │   ├── unit.yaml         # kind/id/version/archetype/payload/requires
    │   ├── backend/          # hubara_agency/src/plugins/<id>/
    │   ├── frontend/         # frontend_dashboard/src/plugins/<id>/ (incluye plugin.yaml)
    │   └── tests/            # hubara_agency/tests/plugins/<id>/ (opcional)
    └── checksums.sha256      # sha256 por archivo (integridad)

Las unidades son self-describing (``unit.yaml``): el sellado agrega TODO lo
que haya en ``units/`` — incluidas unidades foráneas pre-stageadas por otro
sistema (GraphAgents stagea ``graphagent-<id>/`` y este CLI las sella igual;
cada CLI instala SOLO sus kinds). El TCK instanciado NO viaja: se REGENERA en
el destino desde el template del scaffolder (misma fuente que ``create``).

Sin k8s: los YAML file-per-worker no viajan (decisión 2026-07-18 — no se usa).
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PACKAGE_FORMAT = "acktospkg/1"
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# admite ${VAR} y ${VAR:-default} (sintaxis compose)
_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}")
_STAGE_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", ".DS_Store", "node_modules", ".pytest_cache"
)


class PackagingError(ValueError):
    """Input inválido, plugin inexistente o paquete corrupto."""


# ---------------------------------------------------------------------------
# modelo
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PluginUnit:
    """Una unidad exportable: el plugin + lo que exige del destino."""

    plugin_id: str
    version: str
    archetype: str
    depends_on: tuple[str, ...]
    env_vars: tuple[str, ...]
    secrets: tuple[str, ...]
    backend_dir: Path
    frontend_dir: Path
    tests_dir: Path | None


@dataclass(frozen=True)
class ExportPlan:
    requested: tuple[str, ...]
    units: tuple[PluginUnit, ...]

    def only(self, plugin_ids: list[str] | tuple[str, ...]) -> "ExportPlan":
        """Clausura recortada a mano (Studio deja deseleccionar unidades)."""
        keep = set(plugin_ids)
        return ExportPlan(
            requested=self.requested,
            units=tuple(u for u in self.units if u.plugin_id in keep),
        )


@dataclass(frozen=True)
class PackagedUnit:
    """Una unidad tal como viaja en el paquete (leída de su unit.yaml)."""

    kind: str
    unit_id: str
    version: str
    archetype: str
    dir: str
    requires_plugins: tuple[str, ...]
    requires_env_vars: tuple[str, ...]
    requires_secrets: tuple[str, ...]


@dataclass(frozen=True)
class PackageInfo:
    format: str
    name: str
    source: dict
    units: tuple[PackagedUnit, ...]


@dataclass(frozen=True)
class UnitInstallStatus:
    unit_id: str
    kind: str
    action: str  # "new" | "overwrite" | "foreign"
    version: str = "0.0.0"  # la que trae el paquete
    target_version: str | None = None  # la que YA está en el destino (overwrite)
    downgrade: bool = False  # pkg < destino (solo si ambas son semver)


@dataclass(frozen=True)
class InstallPlan:
    units: tuple[UnitInstallStatus, ...]
    missing_plugins: tuple[str, ...]
    post_steps: tuple[str, ...]


@dataclass(frozen=True)
class InstallResult:
    written: tuple[Path, ...]
    installed: tuple[str, ...]
    replaced: tuple[str, ...]
    skipped: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# lectura de manifests del repo (parametrizada por repo_root — sirve para
# el repo central Y para cualquier clon forjado)
# ---------------------------------------------------------------------------

def _manifest_dir(repo_root: Path, plugin_id: str) -> Path:
    return repo_root / "frontend_dashboard" / "src" / "plugins" / plugin_id


def _backend_dir(repo_root: Path, plugin_id: str) -> Path:
    return repo_root / "hubara_agency" / "src" / "plugins" / plugin_id


def _tests_dir(repo_root: Path, plugin_id: str) -> Path:
    return repo_root / "hubara_agency" / "tests" / "plugins" / plugin_id


def _conformance_path(repo_root: Path, plugin_id: str) -> Path:
    return (
        repo_root
        / "hubara_agency"
        / "tests"
        / "conformance"
        / f"test_{plugin_id}_conformance.py"
    )


def _known_plugin_ids(repo_root: Path) -> list[str]:
    plugins_root = repo_root / "frontend_dashboard" / "src" / "plugins"
    if not plugins_root.is_dir():
        return []
    return sorted(
        p.parent.name
        for p in plugins_root.glob("*/plugin.yaml")
        if not p.parent.name.startswith("_")
    )


def _load_raw_manifest(repo_root: Path, plugin_id: str) -> dict:
    path = _manifest_dir(repo_root, plugin_id) / "plugin.yaml"
    if not path.is_file():
        known = ", ".join(_known_plugin_ids(repo_root)) or "(ninguno)"
        raise PackagingError(
            f"plugin inexistente: {plugin_id!r} (sin {path}). Conocidos: {known}"
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("id") != plugin_id:
        raise PackagingError(f"manifest inválido en {path}: id != {plugin_id!r}")
    # Fail-fast: un manifest que no pasa el modelo tipado NO se exporta — el
    # error aparece acá (con el plugin a la vista), no en el certify del destino.
    from src.sdk.manifest_model import ManifestValidationError, parse_manifest

    try:
        parse_manifest(raw, source=plugin_id)
    except ManifestValidationError as exc:
        raise PackagingError(
            f"manifest de {plugin_id!r} no pasa la validación tipada: {exc}"
        ) from exc
    return raw


def _unit_from_manifest(repo_root: Path, plugin_id: str, raw: dict) -> PluginUnit:
    env_vars: set[str] = set()
    secrets: set[str] = set()
    agent = raw.get("agent") or {}
    for worker in agent.get("workers") or []:
        compose_env = (worker.get("compose") or {}).get("env") or {}
        for value in compose_env.values():
            if isinstance(value, str):
                env_vars.update(_ENV_REF_RE.findall(value))
        for entry in (worker.get("deployment") or {}).get("env_secrets") or []:
            var = entry.get("var") if isinstance(entry, dict) else None
            if var:
                secrets.add(var)
    wiring = raw.get("wiring_intents") or {}
    env_vars.update(wiring.get("env_vars_required") or [])

    tests_dir = _tests_dir(repo_root, plugin_id)
    return PluginUnit(
        plugin_id=plugin_id,
        version=str(raw.get("version", "0.0.0")),
        archetype=str(raw.get("archetype", "")),
        depends_on=tuple(raw.get("depends_on") or []),
        env_vars=tuple(sorted(env_vars)),
        secrets=tuple(sorted(secrets)),
        backend_dir=_backend_dir(repo_root, plugin_id),
        frontend_dir=_manifest_dir(repo_root, plugin_id),
        tests_dir=tests_dir if tests_dir.is_dir() else None,
    )


# ---------------------------------------------------------------------------
# plan_export — clausura por depends_on, deps primero
# ---------------------------------------------------------------------------

def plan_export(plugin_ids: list[str] | tuple[str, ...], *, repo_root: Path) -> ExportPlan:
    repo_root = Path(repo_root)
    ordered: list[PluginUnit] = []
    visited: set[str] = set()

    def visit(pid: str, chain: tuple[str, ...]) -> None:
        if pid in chain:
            cycle = " → ".join((*chain, pid))
            raise PackagingError(f"ciclo en depends_on: {cycle}")
        if pid in visited:
            return
        raw = _load_raw_manifest(repo_root, pid)
        unit = _unit_from_manifest(repo_root, pid, raw)
        for dep in unit.depends_on:
            visit(dep, (*chain, pid))
        visited.add(pid)
        ordered.append(unit)

    for pid in plugin_ids:
        visit(pid, ())
    return ExportPlan(requested=tuple(plugin_ids), units=tuple(ordered))


# ---------------------------------------------------------------------------
# stage + build
# ---------------------------------------------------------------------------

def stage_units(plan: ExportPlan, *, staging_dir: Path) -> list[Path]:
    """Escribe ``units/plugin-<id>/`` self-describing en el staging dir."""
    staged: list[Path] = []
    for unit in plan.units:
        unit_dir = staging_dir / "units" / f"plugin-{unit.plugin_id}"
        if unit_dir.exists():
            shutil.rmtree(unit_dir)
        payload: dict[str, str] = {}
        shutil.copytree(unit.frontend_dir, unit_dir / "frontend", ignore=_STAGE_IGNORE)
        payload["frontend"] = "frontend"
        if unit.backend_dir.is_dir():
            shutil.copytree(unit.backend_dir, unit_dir / "backend", ignore=_STAGE_IGNORE)
            payload["backend"] = "backend"
        if unit.tests_dir is not None:
            shutil.copytree(unit.tests_dir, unit_dir / "tests", ignore=_STAGE_IGNORE)
            payload["tests"] = "tests"
        (unit_dir / "unit.yaml").write_text(
            yaml.safe_dump(
                {
                    "kind": "plugin",
                    "id": unit.plugin_id,
                    "version": unit.version,
                    "archetype": unit.archetype,
                    "payload": payload,
                    "requires": {
                        "plugins": list(unit.depends_on),
                        "env_vars": list(unit.env_vars),
                        "secrets": list(unit.secrets),
                    },
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        staged.append(unit_dir)
    return staged


def _git_source(repo_root: Path) -> dict:
    def _run(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", "-C", str(repo_root), *args],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            return out.stdout.strip() or None
        except OSError:
            return None

    return {
        "commit": _run("rev-parse", "HEAD"),
        "repo": _run("remote", "get-url", "origin"),
    }


def _collect_unit_manifests(staging_dir: Path) -> list[dict]:
    entries: list[dict] = []
    units_root = staging_dir / "units"
    if not units_root.is_dir():
        return entries
    for unit_yaml in sorted(units_root.glob("*/unit.yaml")):
        raw = yaml.safe_load(unit_yaml.read_text(encoding="utf-8")) or {}
        entries.append(
            {
                "kind": raw.get("kind", "unknown"),
                "id": raw.get("id", unit_yaml.parent.name),
                "version": raw.get("version", "0.0.0"),
                "archetype": raw.get("archetype", ""),
                "dir": f"units/{unit_yaml.parent.name}",
                "requires": raw.get("requires") or {},
            }
        )
    return entries


def _write_checksums(staging_dir: Path) -> Path:
    lines: list[str] = []
    for path in sorted(staging_dir.rglob("*")):
        if not path.is_file() or path.name == "checksums.sha256":
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(staging_dir).as_posix()}")
    out = staging_dir / "checksums.sha256"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def seal_package(staging_dir: Path, *, out_path: Path, name: str, source: dict) -> Path:
    """package.yaml + checksums + tar.gz sobre TODO lo stageado (propio o foráneo)."""
    units = _collect_unit_manifests(staging_dir)
    if not units:
        raise PackagingError(f"staging vacío: {staging_dir} no tiene units/*/unit.yaml")
    (staging_dir / "package.yaml").write_text(
        yaml.safe_dump(
            {
                "format": PACKAGE_FORMAT,
                "name": name,
                "source": source,
                "units": units,
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _write_checksums(staging_dir)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out_path, "w:gz") as tar:
        for path in sorted(staging_dir.rglob("*")):
            tar.add(path, arcname=path.relative_to(staging_dir).as_posix(), recursive=False)
    return out_path


def build_package(
    plan: ExportPlan,
    *,
    repo_root: Path,
    out_path: Path,
    name: str | None = None,
    staging_dir: Path | None = None,
) -> Path:
    """Stagea las unidades del plan (+ conserva lo pre-stageado) y sella."""
    repo_root = Path(repo_root)
    name = name or "+".join(plan.requested)
    if staging_dir is not None:
        staging_dir = Path(staging_dir)
        staging_dir.mkdir(parents=True, exist_ok=True)
        stage_units(plan, staging_dir=staging_dir)
        return seal_package(
            staging_dir, out_path=out_path, name=name, source=_git_source(repo_root)
        )
    with tempfile.TemporaryDirectory(prefix="acktospkg-") as tmp:
        tmp_path = Path(tmp)
        stage_units(plan, staging_dir=tmp_path)
        return seal_package(
            tmp_path, out_path=out_path, name=name, source=_git_source(repo_root)
        )


# ---------------------------------------------------------------------------
# lectura + verificación
# ---------------------------------------------------------------------------

def _safe_extract(pkg_path: Path, dest: Path) -> None:
    with tarfile.open(pkg_path, "r:gz") as tar:
        for member in tar.getmembers():
            target = (dest / member.name).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise PackagingError(f"paquete malicioso: {member.name!r} escapa del root")
            if member.issym() or member.islnk():
                raise PackagingError(f"paquete inválido: link {member.name!r} no permitido")
        tar.extractall(dest, filter="data")  # miembros ya validados arriba


def _verify_checksums(extracted: Path) -> None:
    sums = extracted / "checksums.sha256"
    if not sums.is_file():
        raise PackagingError("paquete sin checksums.sha256")
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, rel = line.partition("  ")
        path = extracted / rel
        if not path.is_file():
            raise PackagingError(f"integridad: falta {rel!r}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise PackagingError(f"integridad: checksum no coincide en {rel!r}")


def _info_from_extracted(extracted: Path) -> PackageInfo:
    raw = yaml.safe_load((extracted / "package.yaml").read_text(encoding="utf-8")) or {}
    if raw.get("format") != PACKAGE_FORMAT:
        raise PackagingError(
            f"formato no soportado: {raw.get('format')!r} (esperado {PACKAGE_FORMAT})"
        )
    units = []
    for entry in raw.get("units") or []:
        requires = entry.get("requires") or {}
        units.append(
            PackagedUnit(
                kind=str(entry.get("kind", "unknown")),
                unit_id=str(entry.get("id", "")),
                version=str(entry.get("version", "0.0.0")),
                archetype=str(entry.get("archetype", "")),
                dir=str(entry.get("dir", "")),
                requires_plugins=tuple(requires.get("plugins") or []),
                requires_env_vars=tuple(requires.get("env_vars") or []),
                requires_secrets=tuple(requires.get("secrets") or []),
            )
        )
    return PackageInfo(
        format=raw["format"],
        name=str(raw.get("name", "")),
        source=raw.get("source") or {},
        units=tuple(units),
    )


def read_package(pkg_path: Path) -> PackageInfo:
    with tempfile.TemporaryDirectory(prefix="acktospkg-read-") as tmp:
        tmp_path = Path(tmp)
        _safe_extract(Path(pkg_path), tmp_path)
        _verify_checksums(tmp_path)
        return _info_from_extracted(tmp_path)


# ---------------------------------------------------------------------------
# plan_install + install
# ---------------------------------------------------------------------------

POST_STEPS: tuple[str, ...] = (
    "cd frontend_dashboard && npm run plugins:sync",
    "cd hubara_agency && uv run python scripts/render-compose.py",
    "cd hubara_agency && uv run python -m src.sdk.cli certify <ids instalados>",
    "agregar los ids a ENABLED_PLUGINS + provisionar env vars/secrets del plan",
)


def _plugin_exists(repo_root: Path, plugin_id: str) -> bool:
    return (
        (_manifest_dir(repo_root, plugin_id) / "plugin.yaml").is_file()
        or _backend_dir(repo_root, plugin_id).is_dir()
    )


def _target_plugin_version(repo_root: Path, plugin_id: str) -> str | None:
    """Versión del plugin YA instalado en el destino (best-effort)."""
    path = _manifest_dir(repo_root, plugin_id) / "plugin.yaml"
    if not path.is_file():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None
    return str(raw.get("version")) if isinstance(raw, dict) and raw.get("version") else None


def _semver_tuple(version: str) -> tuple[int, int, int] | None:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version)
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def plan_install(pkg_path: Path, *, repo_root: Path) -> InstallPlan:
    repo_root = Path(repo_root)
    info = read_package(pkg_path)
    statuses: list[UnitInstallStatus] = []
    packaged_ids = {u.unit_id for u in info.units if u.kind == "plugin"}
    missing: set[str] = set()
    for unit in info.units:
        if unit.kind != "plugin":
            statuses.append(
                UnitInstallStatus(unit.unit_id, unit.kind, "foreign", version=unit.version)
            )
            continue
        exists = _plugin_exists(repo_root, unit.unit_id)
        target_version = _target_plugin_version(repo_root, unit.unit_id) if exists else None
        pkg_sem = _semver_tuple(unit.version)
        dst_sem = _semver_tuple(target_version) if target_version else None
        statuses.append(
            UnitInstallStatus(
                unit.unit_id,
                "plugin",
                "overwrite" if exists else "new",
                version=unit.version,
                target_version=target_version,
                downgrade=bool(pkg_sem and dst_sem and pkg_sem < dst_sem),
            )
        )
        for dep in unit.requires_plugins:
            if dep not in packaged_ids and not _plugin_exists(repo_root, dep):
                missing.add(dep)
    return InstallPlan(
        units=tuple(statuses),
        missing_plugins=tuple(sorted(missing)),
        post_steps=POST_STEPS,
    )


def _replace_tree(src: Path, dest: Path, written: list[Path]) -> bool:
    """Reemplazo completo del dir destino (propaga deletions). True si escribió."""
    if not src.is_dir():
        return False
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    written.extend(p for p in dest.rglob("*") if p.is_file())
    return True


def install_package(
    pkg_path: Path,
    *,
    repo_root: Path,
    units: list[str] | tuple[str, ...] | None = None,
) -> InstallResult:
    """Extrae las unidades kind=plugin en sus 4 paths single-owner (INV-1)."""
    from src.sdk.cli.scaffold import _conformance

    repo_root = Path(repo_root)
    written: list[Path] = []
    installed: list[str] = []
    replaced: list[str] = []
    skipped: list[str] = []
    with tempfile.TemporaryDirectory(prefix="acktospkg-install-") as tmp:
        tmp_path = Path(tmp)
        _safe_extract(Path(pkg_path), tmp_path)
        _verify_checksums(tmp_path)
        info = _info_from_extracted(tmp_path)
        for unit in info.units:
            if unit.kind != "plugin" or (units is not None and unit.unit_id not in units):
                skipped.append(unit.unit_id)
                continue
            if not _ID_RE.match(unit.unit_id):
                raise PackagingError(f"id de unidad inválido: {unit.unit_id!r}")
            unit_dir = (tmp_path / unit.dir).resolve()
            if not str(unit_dir).startswith(str(tmp_path.resolve())):
                raise PackagingError(f"dir de unidad fuera del paquete: {unit.dir!r}")
            was_present = _plugin_exists(repo_root, unit.unit_id)
            if not _replace_tree(
                unit_dir / "frontend", _manifest_dir(repo_root, unit.unit_id), written
            ):
                raise PackagingError(
                    f"unidad {unit.unit_id!r} sin payload frontend (manifest)"
                )
            _replace_tree(unit_dir / "backend", _backend_dir(repo_root, unit.unit_id), written)
            _replace_tree(unit_dir / "tests", _tests_dir(repo_root, unit.unit_id), written)
            conformance = _conformance_path(repo_root, unit.unit_id)
            conformance.parent.mkdir(parents=True, exist_ok=True)
            conformance.write_text(_conformance(unit.unit_id), encoding="utf-8")
            written.append(conformance)
            (replaced if was_present else installed).append(unit.unit_id)
    return InstallResult(
        written=tuple(written),
        installed=tuple(installed),
        replaced=tuple(replaced),
        skipped=tuple(skipped),
    )
