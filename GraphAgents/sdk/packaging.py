"""Empaquetador/instalador de graph agents — formato ``acktospkg/1``.

El MISMO formato de paquete que ``src.sdk.packaging`` del monorepo (package.yaml
+ units/ + checksums.sha256), re-implementado acá SIN importar su código (la
frontera GraphAgents ↔ monorepo comparte conceptos, no módulos). Cada CLI
instala SOLO sus kinds: este maneja ``kind: graphagent``; las unidades
``kind: plugin`` le son foráneas (y viceversa) — así un solo .acktospkg puede
llevar un plugin hubara + su graph agent y cada lado instala lo suyo.

Una unidad graphagent es file-level (GA-root-relative): el manifest, la
capability (``graphs/<mod>.py``), los dirs de tools (``tools/<id>/`` —
dir-level), los tests golden/build/tool y los fixtures que esos tests
referencian. Instalar NO arrasa los dirs compartidos (graphs/, manifests/,
fixtures/, tests/): copia archivo por archivo; solo ``tools/<id>/`` se
reemplaza completo (es single-owner de la tool).
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

PACKAGE_FORMAT = "acktospkg/1"
_AGENT_REF_RE = re.compile(r"^agent://([a-z0-9][a-z0-9\-]*)@\d+$")
_TOOL_REF_RE = re.compile(r"^([a-z0-9][a-z0-9\-]*)@\d+$")
_FIXTURE_RES = (
    re.compile(r"fixtures/([\w.\-/]+)"),
    re.compile(r"[\"']fixtures[\"']\s*/\s*[\"']([\w.\-/]+)[\"']"),
)
_ALLOWED_ROOTS = ("manifests/", "graphs/", "tools/", "tests/", "fixtures/")


class PackagingError(ValueError):
    """Input inválido, agente inexistente o paquete corrupto."""


# ---------------------------------------------------------------------------
# modelo
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GraphAgentUnit:
    agent_id: str
    kind_file: str  # "agent" | "taskgraph"
    archetype: str
    files: tuple[Path, ...]  # relpaths GA-root-relative (file-level)
    tool_dirs: tuple[str, ...]  # relpaths dir-level (tools/<id>/)
    agents: tuple[str, ...]  # agent:// refs (taskgraph)
    ports: tuple[str, ...]  # consumes
    version: str = "0.0.0"  # `version:` opcional del manifest (release)


@dataclass(frozen=True)
class ExportPlan:
    requested: tuple[str, ...]
    units: tuple[GraphAgentUnit, ...]

    def only(self, agent_ids: list[str] | tuple[str, ...]) -> "ExportPlan":
        keep = set(agent_ids)
        return ExportPlan(
            requested=self.requested,
            units=tuple(u for u in self.units if u.agent_id in keep),
        )


@dataclass(frozen=True)
class PackagedUnit:
    kind: str
    unit_id: str
    version: str
    archetype: str
    dir: str
    requires_agents: tuple[str, ...]
    requires_ports: tuple[str, ...]
    fingerprint: str = ""  # identidad de CONTENIDO del payload (sha256[:16])


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
    action: str  # "new" | "overwrite" | "unchanged" | "foreign"
    version: str = "0.0.0"
    target_version: str | None = None
    downgrade: bool = False
    bump_pending: bool = False  # misma versión declarada, contenido DISTINTO


@dataclass(frozen=True)
class InstallPlan:
    units: tuple[UnitInstallStatus, ...]
    missing_agents: tuple[str, ...]
    post_steps: tuple[str, ...]


@dataclass(frozen=True)
class InstallResult:
    written: tuple[Path, ...]
    installed: tuple[str, ...]
    replaced: tuple[str, ...]
    skipped: tuple[str, ...] = ()
    skipped_unchanged: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# resolución de la clausura
# ---------------------------------------------------------------------------

def _manifest_for(ga_root: Path, agent_id: str) -> tuple[Path, str]:
    agent = ga_root / "manifests" / f"{agent_id}.agent.yaml"
    if agent.is_file():
        return agent, "agent"
    task = ga_root / "manifests" / f"{agent_id}.taskgraph.yaml"
    if task.is_file():
        return task, "taskgraph"
    known = sorted(
        p.name.split(".")[0] for p in (ga_root / "manifests").glob("*.yaml")
    ) if (ga_root / "manifests").is_dir() else []
    raise PackagingError(
        f"agente inexistente: {agent_id!r} (sin manifests/{agent_id}.agent.yaml "
        f"ni .taskgraph.yaml). Conocidos: {', '.join(known) or '(ninguno)'}"
    )


def _capability_files(ga_root: Path, capability: str) -> list[Path]:
    module = capability.split(":", 1)[0]
    rel = Path(*module.split("."))
    candidates = [rel.with_suffix(".py"), rel / "__init__.py"]
    for cand in candidates:
        if (ga_root / cand).is_file():
            if cand.name == "__init__.py":
                base = cand.parent
                return sorted(
                    p.relative_to(ga_root)
                    for p in (ga_root / base).rglob("*.py")
                )
            return [cand]
    raise PackagingError(
        f"capability {capability!r} sin archivo: probé {candidates[0]} y {candidates[1]}"
    )


def _refs_in(node: object) -> list[str]:
    """Todos los `$ref:` (recursivo) de un case yaml — seed + golden."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_refs_in(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_refs_in(item))
    return found


def _cases_for(ga_root: Path, agent_id: str, kind_file: str) -> list[Path]:
    """Los ⚡ cases del viewer (fixtures/cases/) cuyo target es este agente,
    más los fixtures que referencian por `$ref` — sin ellos el agente
    instalado queda sin casos replayables en el catálogo de Studio."""
    cases_dir = ga_root / "fixtures" / "cases"
    if not cases_dir.is_dir():
        return []
    target = f"{'flow' if kind_file == 'taskgraph' else 'agent'}:{agent_id}"
    found: list[Path] = []
    for case_path in sorted(cases_dir.glob("*.case.yaml")):
        try:
            raw = yaml.safe_load(case_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if raw.get("target") != target:
            continue
        found.append(case_path.relative_to(ga_root))
        for ref in _refs_in(raw):
            ref_path = Path(ref)
            if ".." not in ref_path.parts and (ga_root / ref_path).is_file():
                found.append(ref_path)
    return found


def _fixtures_referenced(ga_root: Path, test_files: list[Path]) -> list[Path]:
    found: set[Path] = set()
    for rel in test_files:
        text = (ga_root / rel).read_text(encoding="utf-8", errors="ignore")
        for regex in _FIXTURE_RES:
            for name in regex.findall(text):
                fixture = Path("fixtures") / name
                if (ga_root / fixture).is_file():
                    found.add(fixture)
    return sorted(found)


def _unit_for(ga_root: Path, agent_id: str) -> GraphAgentUnit:
    manifest_path, kind_file = _manifest_for(ga_root, agent_id)
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    files: list[Path] = [manifest_path.relative_to(ga_root)]
    tool_dirs: list[str] = []
    agents: list[str] = []
    tests: list[Path] = []

    if kind_file == "taskgraph":
        for entry in raw.get("agents") or []:
            ref = (entry or {}).get("uses", "")
            match = _AGENT_REF_RE.match(str(ref))
            if not match:
                raise PackagingError(
                    f"{manifest_path.name}: ref inválida {ref!r} (esperado agent://<id>@<major>)"
                )
            agents.append(match.group(1))
    else:
        capability = raw.get("capability")
        if capability:
            cap_files = _capability_files(ga_root, str(capability))
            files.extend(cap_files)
            module_tail = str(capability).split(":", 1)[0].split(".")[-1]
            tests.extend(
                sorted(
                    p.relative_to(ga_root)
                    for p in (ga_root / "tests" / "graphs").glob(
                        f"test_{module_tail}*.py"
                    )
                )
            )
        for entry in raw.get("tools") or []:
            ref = (entry or {}).get("uses", "")
            match = _TOOL_REF_RE.match(str(ref))
            if not match:
                raise PackagingError(
                    f"{manifest_path.name}: tool ref inválida {ref!r} (esperado <id>@<major>)"
                )
            tool_dir = Path("tools") / match.group(1).replace("-", "_")
            if not (ga_root / tool_dir).is_dir():
                raise PackagingError(
                    f"{manifest_path.name}: tool {ref!r} sin dir {tool_dir}/"
                )
            tool_dirs.append(tool_dir.as_posix())
            tool_test = (
                Path("tests") / "tools" / f"test_{tool_dir.name}.py"
            )
            if (ga_root / tool_test).is_file():
                tests.append(tool_test)
        conformance = (
            Path("tests")
            / "conformance"
            / f"test_{agent_id.replace('-', '_')}_conformance.py"
        )
        if (ga_root / conformance).is_file():
            tests.append(conformance)

    files.extend(tests)
    files.extend(_fixtures_referenced(ga_root, tests))
    files.extend(_cases_for(ga_root, agent_id, kind_file))
    return GraphAgentUnit(
        agent_id=agent_id,
        kind_file=kind_file,
        archetype=str(raw.get("archetype", "")),
        files=tuple(dict.fromkeys(files)),
        tool_dirs=tuple(dict.fromkeys(tool_dirs)),
        agents=tuple(agents),
        ports=tuple(raw.get("consumes") or []),
        version=str(raw.get("version") or "0.0.0"),
    )


def plan_export(agent_ids: list[str] | tuple[str, ...], *, ga_root: Path) -> ExportPlan:
    ga_root = Path(ga_root)
    ordered: list[GraphAgentUnit] = []
    visited: set[str] = set()

    def visit(aid: str, chain: tuple[str, ...]) -> None:
        if aid in chain:
            raise PackagingError(f"ciclo en agent:// refs: {' → '.join((*chain, aid))}")
        if aid in visited:
            return
        unit = _unit_for(ga_root, aid)
        for dep in unit.agents:
            visit(dep, (*chain, aid))
        visited.add(aid)
        ordered.append(unit)

    for aid in agent_ids:
        visit(aid, ())
    return ExportPlan(requested=tuple(agent_ids), units=tuple(ordered))


# ---------------------------------------------------------------------------
# fingerprint de contenido — mismo criterio de ambos lados (staged y destino)
# ---------------------------------------------------------------------------

_IGNORE_DIR_NAMES = {"__pycache__", ".DS_Store", "node_modules", ".pytest_cache"}


def _dir_files(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    out = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix == ".pyc":
            continue
        rel = path.relative_to(base)
        if any(part in _IGNORE_DIR_NAMES for part in rel.parts):
            continue
        out.append(path)
    return out


def _fingerprint_payload(base: Path, files: list[str], dirs: list[str]) -> str:
    """sha256[:16] del payload — `base` es el unit dir staged O el ga_root."""
    digest = hashlib.sha256()
    for rel in sorted(files):
        path = base / rel
        digest.update(f"{rel}\0".encode())
        digest.update(path.read_bytes() if path.is_file() else b"<missing>")
        digest.update(b"\0")
    for rel in sorted(dirs):
        for path in _dir_files(base / rel):
            file_rel = f"{rel}/{path.relative_to(base / rel).as_posix()}"
            digest.update(f"{file_rel}\0".encode())
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()[:16]


def _semver_tuple(version: str) -> tuple[int, int, int] | None:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version)
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


# ---------------------------------------------------------------------------
# stage + seal (formato compartido con el CLI hubara)
# ---------------------------------------------------------------------------

def stage_units(plan: ExportPlan, *, ga_root: Path, staging_dir: Path) -> list[Path]:
    ga_root = Path(ga_root)
    staged: list[Path] = []
    for unit in plan.units:
        unit_dir = Path(staging_dir) / "units" / f"graphagent-{unit.agent_id}"
        if unit_dir.exists():
            shutil.rmtree(unit_dir)
        for rel in unit.files:
            dest = unit_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ga_root / rel, dest)
        for rel in unit.tool_dirs:
            shutil.copytree(
                ga_root / rel,
                unit_dir / rel,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
            )
        payload_files = [f.as_posix() for f in unit.files]
        fingerprint = _fingerprint_payload(unit_dir, payload_files, list(unit.tool_dirs))
        (unit_dir / "unit.yaml").write_text(
            yaml.safe_dump(
                {
                    "kind": "graphagent",
                    "id": unit.agent_id,
                    "version": unit.version,
                    "archetype": unit.archetype,
                    "fingerprint": fingerprint,
                    "payload": {
                        "files": payload_files,
                        "dirs": list(unit.tool_dirs),
                    },
                    "requires": {
                        "agents": list(unit.agents),
                        "ports": list(unit.ports),
                    },
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        staged.append(unit_dir)
    return staged


def _git_source(ga_root: Path) -> dict:
    def _run(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", "-C", str(ga_root), *args],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            return out.stdout.strip() or None
        except OSError:
            return None

    return {"commit": _run("rev-parse", "HEAD"), "repo": _run("remote", "get-url", "origin")}


def _write_checksums(staging_dir: Path) -> None:
    lines = []
    for path in sorted(staging_dir.rglob("*")):
        if not path.is_file() or path.name == "checksums.sha256":
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(staging_dir).as_posix()}")
    (staging_dir / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _collect_unit_manifests(staging_dir: Path) -> list[dict]:
    entries = []
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
                "fingerprint": raw.get("fingerprint", ""),
                "dir": f"units/{unit_yaml.parent.name}",
                "requires": raw.get("requires") or {},
            }
        )
    return entries


def seal_package(staging_dir: Path, *, out_path: Path, name: str, source: dict) -> Path:
    staging_dir = Path(staging_dir)
    units = _collect_unit_manifests(staging_dir)
    if not units:
        raise PackagingError(f"staging vacío: {staging_dir} no tiene units/*/unit.yaml")
    (staging_dir / "package.yaml").write_text(
        yaml.safe_dump(
            {"format": PACKAGE_FORMAT, "name": name, "source": source, "units": units},
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
    ga_root: Path,
    out_path: Path,
    name: str | None = None,
    staging_dir: Path | None = None,
) -> Path:
    ga_root = Path(ga_root)
    name = name or "+".join(plan.requested)
    if staging_dir is not None:
        staging_dir = Path(staging_dir)
        staging_dir.mkdir(parents=True, exist_ok=True)
        stage_units(plan, ga_root=ga_root, staging_dir=staging_dir)
        return seal_package(staging_dir, out_path=out_path, name=name, source=_git_source(ga_root))
    with tempfile.TemporaryDirectory(prefix="acktospkg-ga-") as tmp:
        tmp_path = Path(tmp)
        stage_units(plan, ga_root=ga_root, staging_dir=tmp_path)
        return seal_package(tmp_path, out_path=out_path, name=name, source=_git_source(ga_root))


# ---------------------------------------------------------------------------
# lectura + instalación
# ---------------------------------------------------------------------------

def _safe_extract(pkg_path: Path, dest: Path) -> None:
    with tarfile.open(pkg_path, "r:gz") as tar:
        for member in tar.getmembers():
            target = (dest / member.name).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise PackagingError(f"paquete malicioso: {member.name!r} escapa del root")
            if member.issym() or member.islnk():
                raise PackagingError(f"paquete inválido: link {member.name!r} no permitido")
        tar.extractall(dest, filter="data")


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
                requires_agents=tuple(requires.get("agents") or []),
                requires_ports=tuple(requires.get("ports") or []),
                fingerprint=str(entry.get("fingerprint") or ""),
            )
        )
    return PackageInfo(
        format=raw["format"],
        name=str(raw.get("name", "")),
        source=raw.get("source") or {},
        units=tuple(units),
    )


def read_package(pkg_path: Path) -> PackageInfo:
    with tempfile.TemporaryDirectory(prefix="acktospkg-ga-read-") as tmp:
        tmp_path = Path(tmp)
        _safe_extract(Path(pkg_path), tmp_path)
        _verify_checksums(tmp_path)
        return _info_from_extracted(tmp_path)


def _unit_payload(extracted: Path, unit: PackagedUnit) -> tuple[list[str], list[str]]:
    unit_yaml = extracted / unit.dir / "unit.yaml"
    raw = yaml.safe_load(unit_yaml.read_text(encoding="utf-8")) or {}
    payload = raw.get("payload") or {}
    files = [str(f) for f in payload.get("files") or []]
    dirs = [str(d) for d in payload.get("dirs") or []]
    for rel in (*files, *dirs):
        if ".." in Path(rel).parts or not rel.startswith(_ALLOWED_ROOTS):
            raise PackagingError(f"payload fuera de los roots permitidos: {rel!r}")
    return files, dirs


POST_STEPS: tuple[str, ...] = (
    "cd GraphAgents && python3 -m sdk.cli check",
    "cd GraphAgents && python3 -m sdk.cli certify <ids instalados>",
    "cd GraphAgents && python3 -m sdk.cli cases --check",
)


def _target_manifest_version(ga_root: Path, manifest_rel: str | None) -> str | None:
    if not manifest_rel or not (ga_root / manifest_rel).is_file():
        return None
    try:
        raw = yaml.safe_load((ga_root / manifest_rel).read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None
    return str(raw.get("version")) if isinstance(raw, dict) and raw.get("version") else None


def plan_install(pkg_path: Path, *, ga_root: Path) -> InstallPlan:
    ga_root = Path(ga_root)
    statuses: list[UnitInstallStatus] = []
    missing: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="acktospkg-ga-plan-") as tmp:
        tmp_path = Path(tmp)
        _safe_extract(Path(pkg_path), tmp_path)
        _verify_checksums(tmp_path)
        info = _info_from_extracted(tmp_path)
        packaged = {u.unit_id for u in info.units if u.kind == "graphagent"}
        for unit in info.units:
            if unit.kind != "graphagent":
                statuses.append(
                    UnitInstallStatus(unit.unit_id, unit.kind, "foreign", version=unit.version)
                )
                continue
            files, dirs = _unit_payload(tmp_path, unit)
            manifest_rel = next((f for f in files if f.startswith("manifests/")), None)
            exists = bool(manifest_rel) and (ga_root / manifest_rel).is_file()
            unchanged = bool(
                exists
                and unit.fingerprint
                and _fingerprint_payload(ga_root, files, dirs) == unit.fingerprint
            )
            target_version = _target_manifest_version(ga_root, manifest_rel) if exists else None
            pkg_sem = _semver_tuple(unit.version)
            dst_sem = _semver_tuple(target_version) if target_version else None
            statuses.append(
                UnitInstallStatus(
                    unit.unit_id,
                    "graphagent",
                    "unchanged" if unchanged else ("overwrite" if exists else "new"),
                    version=unit.version,
                    target_version=target_version,
                    downgrade=bool(
                        not unchanged and pkg_sem and dst_sem and pkg_sem < dst_sem
                    ),
                    # sin `version:` declarada, la efectiva es 0.0.0 — así un
                    # agente nunca versionado también avisa que falta el bump
                    bump_pending=bool(
                        not unchanged
                        and exists
                        and unit.version == (target_version or "0.0.0")
                    ),
                )
            )
            for dep in unit.requires_agents:
                if dep not in packaged and not _agent_exists(ga_root, dep):
                    missing.add(dep)
    return InstallPlan(
        units=tuple(statuses),
        missing_agents=tuple(sorted(missing)),
        post_steps=POST_STEPS,
    )


def _agent_exists(ga_root: Path, agent_id: str) -> bool:
    manifests = ga_root / "manifests"
    return (manifests / f"{agent_id}.agent.yaml").is_file() or (
        manifests / f"{agent_id}.taskgraph.yaml"
    ).is_file()


def _ledger_path(ga_root: Path) -> Path:
    return ga_root / "installed-packages.yaml"


def _append_ledger(ga_root: Path, entries: list[dict]) -> Path | None:
    """Libro de instalaciones del GA root destino (histórico de despliegues)."""
    if not entries:
        return None
    path = _ledger_path(ga_root)
    ledger = {"version": 1, "installs": []}
    if path.is_file():
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("installs"), list):
            ledger = raw
    ledger["installs"].extend(entries)
    path.write_text(
        yaml.safe_dump(ledger, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return path


def install_package(
    pkg_path: Path,
    *,
    ga_root: Path,
    units: list[str] | tuple[str, ...] | None = None,
) -> InstallResult:
    """Copia file-level en los dirs compartidos; ``tools/<id>/`` dir-level.

    Idempotente: una unidad cuyo contenido YA está en el destino (mismo
    fingerprint) se saltea sin escribir ni appendear al ledger.
    """

    ga_root = Path(ga_root)
    written: list[Path] = []
    installed: list[str] = []
    replaced: list[str] = []
    skipped: list[str] = []
    skipped_unchanged: list[str] = []
    ledger_entries: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="acktospkg-ga-install-") as tmp:
        tmp_path = Path(tmp)
        _safe_extract(Path(pkg_path), tmp_path)
        _verify_checksums(tmp_path)
        info = _info_from_extracted(tmp_path)
        for unit in info.units:
            if unit.kind != "graphagent" or (units is not None and unit.unit_id not in units):
                skipped.append(unit.unit_id)
                continue
            files, dirs = _unit_payload(tmp_path, unit)
            manifest_rel = next((f for f in files if f.startswith("manifests/")), None)
            was_present = bool(manifest_rel) and (ga_root / manifest_rel).is_file()
            if (
                was_present
                and unit.fingerprint
                and _fingerprint_payload(ga_root, files, dirs) == unit.fingerprint
            ):
                skipped_unchanged.append(unit.unit_id)
                continue
            unit_dir = tmp_path / unit.dir
            for rel in files:
                src = unit_dir / rel
                if not src.is_file():
                    raise PackagingError(f"unidad {unit.unit_id!r}: falta payload {rel!r}")
                dest = ga_root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                written.append(dest)
            for rel in dirs:
                src = unit_dir / rel
                if not src.is_dir():
                    raise PackagingError(f"unidad {unit.unit_id!r}: falta payload dir {rel!r}")
                dest = ga_root / rel
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(src, dest)
                written.extend(p for p in dest.rglob("*") if p.is_file())
            (replaced if was_present else installed).append(unit.unit_id)
            ledger_entries.append(
                {
                    "unit": unit.unit_id,
                    "kind": "graphagent",
                    "version": unit.version,
                    "fingerprint": unit.fingerprint,
                    "package": info.name,
                    "source_commit": info.source.get("commit"),
                    "installed_at": datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                }
            )
        ledger = _append_ledger(ga_root, ledger_entries)
        if ledger is not None:
            written.append(ledger)
    return InstallResult(
        written=tuple(written),
        installed=tuple(installed),
        replaced=tuple(replaced),
        skipped=tuple(skipped),
        skipped_unchanged=tuple(skipped_unchanged),
    )
