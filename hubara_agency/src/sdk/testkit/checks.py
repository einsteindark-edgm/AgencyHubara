"""Checks del TCK — funciones puras (filesystem-only) que emiten CheckResults.

Cada check toma un ``CheckContext`` y devuelve ``list[CheckResult]``; cada
result referencia un código del catálogo de diagnósticos
(``src.sdk.diagnostics``). Los consumen tres frontends con la MISMA fuente:

- ``conformance_suite()`` → tests pytest por plugin (tests/conformance/);
- ``run_conformance()`` → reporte JSON de certificación (CLI / CI / catálogo);
- ``python -m src.sdk.cli check`` → salida estilo rustc.

Diseño: cero red, cero imports de módulos del plugin (solo AST + disco) —
un check no puede colgarse ni ejecutar side effects. El ``CheckContext``
lleva ``repo_root`` inyectable para que el golden test del scaffolder corra
hermético en un tmpdir.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from src.sdk.manifest_model import (
    ARCHETYPES,
    ManifestValidationError,
    PluginManifest,
    parse_manifest,
)
from src.sdk.testkit.archetypes import PROFILES, ArchetypeProfile

Status = Literal["pass", "fail", "warn", "skip"]


@dataclass(frozen=True)
class CheckResult:
    code: str
    name: str
    status: Status
    detail: str = ""
    location: str = ""


@dataclass(frozen=True)
class CheckContext:
    plugin_id: str
    repo_root: Path
    raw_manifest: dict[str, Any]
    typed: PluginManifest | None  # None ⇒ C0 falló
    c0_error: str = ""
    all_plugin_ids: frozenset[str] = field(default_factory=frozenset)

    @property
    def manifests_dir(self) -> Path:
        return self.repo_root / "frontend_dashboard" / "src" / "plugins"

    @property
    def manifest_dir(self) -> Path:
        return self.manifests_dir / self.plugin_id

    @property
    def backend_dir(self) -> Path:
        return self.repo_root / "hubara_agency" / "src" / "plugins" / self.plugin_id

    @property
    def conformance_dir(self) -> Path:
        return self.repo_root / "hubara_agency" / "tests" / "conformance"


def build_context(plugin_id: str, repo_root: Path | None = None) -> CheckContext:
    """Arma el contexto leyendo el manifest del disco (sin imports de código)."""
    import yaml

    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[4]
    manifests_dir = repo_root / "frontend_dashboard" / "src" / "plugins"
    manifest_path = manifests_dir / plugin_id / "plugin.yaml"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"plugin {plugin_id!r}: no existe {manifest_path} (P-26)"
        )
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    typed: PluginManifest | None
    c0_error = ""
    try:
        typed = parse_manifest(raw, source=f"plugin {plugin_id!r}")
    except ManifestValidationError as exc:
        typed = None
        c0_error = str(exc)
    ids = frozenset(
        p.name
        for p in manifests_dir.iterdir()
        if p.is_dir() and not p.name.startswith(("_", ".")) and (p / "plugin.yaml").exists()
    )
    return CheckContext(
        plugin_id=plugin_id,
        repo_root=repo_root,
        raw_manifest=raw,
        typed=typed,
        c0_error=c0_error,
        all_plugin_ids=ids,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _module_path(ctx: CheckContext, module: str) -> Path | None:
    """``src.plugins.x.api`` → path real bajo hubara_agency (None si no es src.*)."""
    if not module.startswith("src."):
        return None
    base = ctx.repo_root / "hubara_agency" / Path(*module.split("."))
    if base.with_suffix(".py").exists():
        return base.with_suffix(".py")
    if (base / "__init__.py").exists():
        return base / "__init__.py"
    return base  # no existe — el caller reporta


def _module_exists(ctx: CheckContext, module: str) -> bool:
    p = _module_path(ctx, module)
    return p is not None and p.exists() and p.suffix == ".py"


def _ok(code: str, name: str, detail: str = "") -> CheckResult:
    return CheckResult(code, name, "pass", detail)


def _fail(code: str, name: str, detail: str, location: str = "") -> CheckResult:
    return CheckResult(code, name, "fail", detail, location)


def _warn(code: str, name: str, detail: str, location: str = "") -> CheckResult:
    return CheckResult(code, name, "warn", detail, location)


# ---------------------------------------------------------------------------
# C0 — Declarado
# ---------------------------------------------------------------------------

def check_c0_schema(ctx: CheckContext) -> list[CheckResult]:
    if ctx.typed is None:
        return [_fail("C0-SCHEMA", "manifest-valida", ctx.c0_error)]
    return [_ok("C0-SCHEMA", "manifest-valida")]


def check_p29a_archetype(ctx: CheckContext) -> list[CheckResult]:
    if ctx.typed is None:
        return [CheckResult("P-29A", "archetype-declarado", "skip", "C0 falló")]
    if ctx.typed.archetype not in ARCHETYPES:
        return [
            _fail(
                "P-29A",
                "archetype-declarado",
                f"archetype={ctx.typed.archetype!r}; válidos: {', '.join(ARCHETYPES)}",
                location=f"{ctx.manifest_dir / 'plugin.yaml'}",
            )
        ]
    return [_ok("P-29A", "archetype-declarado", ctx.typed.archetype)]


# ---------------------------------------------------------------------------
# C1 — Cargable (lo declarado existe)
# ---------------------------------------------------------------------------

def check_c1_deps(ctx: CheckContext) -> list[CheckResult]:
    if ctx.typed is None:
        return [CheckResult("C1-DEPS", "deps-existen", "skip", "C0 falló")]
    missing = sorted(d for d in ctx.typed.depends_on if d not in ctx.all_plugin_ids)
    if missing:
        return [
            _fail(
                "C1-DEPS",
                "deps-existen",
                f"depends_on referencia plugins inexistentes: {missing}",
            )
        ]
    return [_ok("C1-DEPS", "deps-existen")]


def check_c1_api_modules(ctx: CheckContext) -> list[CheckResult]:
    if ctx.typed is None or ctx.typed.api is None:
        return [CheckResult("C1-API-MODULE", "api-modules-existen", "skip", "sin api")]
    out: list[CheckResult] = []
    modules = [m for m in [ctx.typed.api.python_module] if m]
    modules += [r.module for r in ctx.typed.api.legacy_routers]
    for module in modules:
        if module.startswith("src.") and not _module_exists(ctx, module):
            out.append(
                _fail(
                    "C1-API-MODULE",
                    "api-modules-existen",
                    f"módulo declarado no existe en disco: {module}",
                )
            )
    return out or [_ok("C1-API-MODULE", "api-modules-existen", f"{len(modules)} módulos")]


def check_c1_worker_modules(ctx: CheckContext) -> list[CheckResult]:
    if ctx.typed is None or ctx.typed.agent is None or not ctx.typed.agent.workers:
        return [CheckResult("C1-WORKER-MODULE", "worker-modules-existen", "skip", "sin workers")]
    out: list[CheckResult] = []
    for w in ctx.typed.agent.workers:
        if not _module_exists(ctx, w.module):
            out.append(
                _fail(
                    "C1-WORKER-MODULE",
                    "worker-modules-existen",
                    f"worker {w.name!r}: módulo no existe en disco: {w.module}",
                )
            )
    return out or [_ok("C1-WORKER-MODULE", "worker-modules-existen")]


def check_c1_frontend_entry(ctx: CheckContext) -> list[CheckResult]:
    if ctx.typed is None or ctx.typed.frontend is None:
        return [CheckResult("C1-FRONTEND-ENTRY", "frontend-entry-existe", "skip", "sin frontend")]
    entry_dir = (ctx.manifest_dir / ctx.typed.frontend.entry).resolve()
    candidates = [entry_dir / "index.ts", entry_dir / "index.tsx"]
    if not any(c.exists() for c in candidates):
        return [
            _fail(
                "C1-FRONTEND-ENTRY",
                "frontend-entry-existe",
                f"no existe {entry_dir / 'index.ts'} (default-export del Page)",
            )
        ]
    return [_ok("C1-FRONTEND-ENTRY", "frontend-entry-existe")]


# ---------------------------------------------------------------------------
# P-rules per-plugin (espejo liviano de los gates centrales, para el reporte)
# ---------------------------------------------------------------------------

def check_p15_workspaces(ctx: CheckContext) -> list[CheckResult]:
    if ctx.typed is None or ctx.typed.agent is None:
        return [CheckResult("P-15", "workspaces-existen", "skip", "sin workers")]
    out: list[CheckResult] = []
    for w in ctx.typed.agent.workers:
        if w.dashboard and w.dashboard.workspace:
            ws = ctx.repo_root / w.dashboard.workspace
            if not ws.is_dir():
                out.append(
                    _fail(
                        "P-15",
                        "workspaces-existen",
                        f"worker {w.name!r}: dashboard.workspace no existe: {w.dashboard.workspace}",
                    )
                )
    return out or [_ok("P-15", "workspaces-existen")]


def check_p17_agentic_coherence(ctx: CheckContext) -> list[CheckResult]:
    if ctx.typed is None:
        return [CheckResult("P-17", "agentic-coherente", "skip", "C0 falló")]
    workers = ctx.typed.agent.workers if ctx.typed.agent else []
    has_dashboard = any(w.dashboard is not None for w in workers)
    if ctx.typed.agentic != has_dashboard:
        return [
            _fail(
                "P-17",
                "agentic-coherente",
                f"agentic={ctx.typed.agentic} pero "
                f"{'hay' if has_dashboard else 'no hay'} workers con dashboard:",
            )
        ]
    return [_ok("P-17", "agentic-coherente")]


def check_p16_queue_selfref(ctx: CheckContext) -> list[CheckResult]:
    """``get_task_queue("<otro>", ...)`` con literal ajeno dentro del plugin."""
    out: list[CheckResult] = []
    if not ctx.backend_dir.exists():
        return [CheckResult("P-16", "queue-self-ref", "skip", "sin backend")]
    for py in sorted(ctx.backend_dir.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (
                fn.attr if isinstance(fn, ast.Attribute) else ""
            )
            if name != "get_task_queue" or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                if first.value != ctx.plugin_id:
                    out.append(
                        _fail(
                            "P-16",
                            "queue-self-ref",
                            f'get_task_queue("{first.value}", ...) — queue ajena',
                            location=str(py.relative_to(ctx.repo_root)),
                        )
                    )
    return out or [_ok("P-16", "queue-self-ref")]


def check_p21_selfgate(ctx: CheckContext) -> list[CheckResult]:
    """Primera sentencia de ``main()`` del worker = ``ensure_plugin_enabled('<id>')``."""
    if ctx.typed is None or ctx.typed.agent is None or not ctx.typed.agent.workers:
        return [CheckResult("P-21", "worker-self-gate", "skip", "sin workers")]
    out: list[CheckResult] = []
    for w in ctx.typed.agent.workers:
        path = _module_path(ctx, w.module)
        if path is None or not path.exists() or path.suffix != ".py":
            continue  # C1-WORKER-MODULE ya lo reporta
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        ok = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == "main":
                body = [
                    s
                    for s in node.body
                    if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
                ]
                if body:
                    first = body[0]
                    call = first.value if isinstance(first, ast.Expr) else None
                    if isinstance(call, ast.Call):
                        fn = call.func
                        fname = fn.id if isinstance(fn, ast.Name) else (
                            fn.attr if isinstance(fn, ast.Attribute) else ""
                        )
                        if (
                            fname == "ensure_plugin_enabled"
                            and call.args
                            and isinstance(call.args[0], ast.Constant)
                            and call.args[0].value == ctx.plugin_id
                        ):
                            ok = True
        if not ok:
            out.append(
                _fail(
                    "P-21",
                    "worker-self-gate",
                    f"worker {w.name!r}: main() no arranca con "
                    f'ensure_plugin_enabled("{ctx.plugin_id}")',
                    location=str(path.relative_to(ctx.repo_root)),
                )
            )
    return out or [_ok("P-21", "worker-self-gate")]


def check_p27_conformance_file(ctx: CheckContext) -> list[CheckResult]:
    path = ctx.conformance_dir / f"test_{ctx.plugin_id}_conformance.py"
    if not path.exists():
        return [
            _fail(
                "P-27",
                "tck-instanciado",
                f"falta {path.name} — el plugin no instancia el TCK",
                location=str(path),
            )
        ]
    text = path.read_text(encoding="utf-8")
    if not re.search(rf"conformance_suite\(\s*['\"]{ctx.plugin_id}['\"]\s*\)", text):
        return [
            _fail(
                "P-27",
                "tck-instanciado",
                f'{path.name} existe pero no invoca conformance_suite("{ctx.plugin_id}")',
                location=str(path),
            )
        ]
    return [_ok("P-27", "tck-instanciado")]


# ---------------------------------------------------------------------------
# P-29 — el perfil del arquetipo
# ---------------------------------------------------------------------------

def _check_presence(
    what: str, presence: str, has_it: bool
) -> tuple[bool, str] | None:
    if presence == "required" and not has_it:
        return False, f"el perfil exige {what} y el manifest no lo declara"
    if presence == "forbidden" and has_it:
        return False, f"el perfil PROHÍBE {what} y el manifest lo declara"
    return None


def check_p29_profile(ctx: CheckContext) -> list[CheckResult]:
    if ctx.typed is None or ctx.typed.archetype is None:
        return [CheckResult("P-29", "perfil-arquetipo", "skip", "sin archetype (P-29A)")]
    profile: ArchetypeProfile = PROFILES[ctx.typed.archetype]
    out: list[CheckResult] = []
    typed = ctx.typed
    workers = typed.agent.workers if typed.agent else []

    for what, presence, has_it in (
        ("frontend:", profile.frontend, typed.frontend is not None),
        ("api:", profile.api, typed.api is not None),
        ("workers", profile.workers, bool(workers)),
    ):
        bad = _check_presence(what, presence, has_it)
        if bad:
            out.append(_fail("P-29", f"perfil-{profile.name}", bad[1]))

    if profile.agentic_flag is not None and typed.agentic != profile.agentic_flag:
        out.append(
            _fail(
                "P-29",
                f"perfil-{profile.name}",
                f"el perfil exige agentic={profile.agentic_flag} (got {typed.agentic})",
            )
        )
    if profile.require_worker_dashboard and not any(w.dashboard for w in workers):
        out.append(
            _fail(
                "P-29",
                f"perfil-{profile.name}",
                "el perfil exige ≥1 worker con dashboard: (agente visible)",
            )
        )
    if profile.forbid_worker_dashboard and any(w.dashboard for w in workers):
        out.append(
            _fail(
                "P-29",
                f"perfil-{profile.name}",
                "el perfil PROHÍBE dashboard: en workers (un pipeline no es un agente)",
            )
        )
    if profile.forbid_owns_route:
        owners = [w.name for w in workers if w.owns_route]
        if owners:
            out.append(
                _fail(
                    "P-29",
                    f"perfil-{profile.name}",
                    f"el perfil PROHÍBE owns_route (L-4: notificar/sincronizar ≠ "
                    f"poseer la conversación); lo declaran: {owners}",
                )
            )

    for rule in profile.required_globs:
        if not ctx.backend_dir.exists() or not any(
            p.is_dir() for p in ctx.backend_dir.glob(rule.pattern)
        ):
            out.append(
                _fail(
                    "P-29",
                    f"perfil-{profile.name}",
                    f"estructura faltante: src/plugins/{ctx.plugin_id}/{rule.pattern} "
                    f"({rule.why})",
                )
            )
    for rule in profile.advisory_globs:
        if not ctx.backend_dir.exists() or not any(
            p.is_dir() for p in ctx.backend_dir.glob(rule.pattern)
        ):
            out.append(
                _warn(
                    "P-29",
                    f"perfil-{profile.name}",
                    f"recomendado (migración pendiente): src/plugins/{ctx.plugin_id}/"
                    f"{rule.pattern} ({rule.why})",
                )
            )
    return out or [_ok("P-29", f"perfil-{profile.name}")]


# ---------------------------------------------------------------------------
# La suite
# ---------------------------------------------------------------------------

ALL_CHECKS: tuple[Callable[[CheckContext], list[CheckResult]], ...] = (
    check_c0_schema,
    check_p29a_archetype,
    check_c1_deps,
    check_c1_api_modules,
    check_c1_worker_modules,
    check_c1_frontend_entry,
    check_p15_workspaces,
    check_p17_agentic_coherence,
    check_p16_queue_selfref,
    check_p21_selfgate,
    check_p27_conformance_file,
    check_p29_profile,
)


def run_all_checks(ctx: CheckContext) -> list[CheckResult]:
    results: list[CheckResult] = []
    for check in ALL_CHECKS:
        results.extend(check(ctx))
    return results
