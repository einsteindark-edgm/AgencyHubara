"""R-DET — Workflow determinism rule.

Workflows must NOT contain any non-deterministic side effect: `time.time()`,
`datetime.now()`, `uuid.uuid4()`, `random.*`, `open(...)`, `os.environ`, nor
import I/O libs (`httpx`, `requests`, `litellm`, `exoclaw_conversation`).

The DEHA convention is to wrap repo imports in
`with workflow.unsafe.imports_passed_through():` and use Temporal's deterministic
APIs at runtime (`workflow.now()`, `workflow.uuid4()`, `workflow.sleep(...)`).

Tests:
  #1a — `test_workflows_dont_import_io_libs` (module-level import check)
  #1b — `test_workflows_dont_call_non_deterministic_apis` (call-site AST check)
  #2  — `test_workflows_wrap_repo_imports_in_imports_passed_through`
"""
from __future__ import annotations

import ast
from pathlib import Path

from tests.architecture.conftest import (
    AGENT_WORKFLOWS_GLOB,
    iter_agent_files,
    parse_file,
    relative_to_hubara,
)


# Importing these in a workflow has no determinism-safe use case. Hard ban.
_FORBIDDEN_IO_IMPORTS: frozenset[str] = frozenset(
    {
        "httpx",
        "requests",
        "litellm",
        "exoclaw_conversation",
        "os",
    }
)

# (module, attribute) pairs that name a non-deterministic call site. Examples:
#   time.time, time.monotonic, datetime.datetime.now, uuid.uuid4, random.choice.
# Workflows can still import `datetime` for `timedelta` (a deterministic struct)
# or for type annotations — what we forbid is the *call*.
_FORBIDDEN_CALLS: frozenset[tuple[str, str]] = frozenset(
    {
        ("time", "time"),
        ("time", "monotonic"),
        ("time", "monotonic_ns"),
        ("time", "perf_counter"),
        ("time", "sleep"),
        ("datetime", "now"),
        ("datetime", "utcnow"),
        ("datetime", "today"),
        ("uuid", "uuid1"),
        ("uuid", "uuid4"),
        ("uuid", "uuid5"),
    }
)

# Bare-name builtin calls that are forbidden anywhere in workflow code.
_FORBIDDEN_BUILTIN_CALLS: frozenset[str] = frozenset({"open"})

# Module prefixes whose *any* attribute access is forbidden at runtime.
_FORBIDDEN_MODULE_ANY_CALL: frozenset[str] = frozenset({"random"})


def _workflow_files() -> list[Path]:
    """Workflow modules (excluding `__init__.py`, which is a re-export shim and
    is not executed inside the Temporal sandbox)."""
    return [p for p in iter_agent_files(AGENT_WORKFLOWS_GLOB) if p.name != "__init__.py"]


def _import_top_levels(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name.split(".")[0] for alias in node.names]
    if node.level > 0 or node.module is None:
        return []
    return [node.module.split(".")[0]]


def _build_parent_map(root: ast.Module) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(root):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _is_inside_unsafe_block(node: ast.AST, parents: dict[int, ast.AST]) -> bool:
    """True si `node` está dentro de `with workflow.unsafe.imports_passed_through():`."""
    cursor: ast.AST | None = node
    while cursor is not None:
        if isinstance(cursor, ast.With):
            for item in cursor.items:
                expr = item.context_expr
                if (
                    isinstance(expr, ast.Call)
                    and isinstance(expr.func, ast.Attribute)
                    and expr.func.attr == "imports_passed_through"
                ):
                    return True
        cursor = parents.get(id(cursor))
    return False


# ----------------------------------------------------------------------------
# Test #1a — imports de libs de I/O prohibidos a nivel módulo.
# ----------------------------------------------------------------------------

def test_workflows_dont_import_io_libs() -> None:
    """Workflows no pueden importar libs de I/O ni `os`. No hay caso legítimo.

    `datetime` y `time` SÍ están permitidos como import (para tipos o constantes
    como `timedelta`); el check de uso vive en `test_workflows_dont_call_*`.
    """
    violations: list[str] = []
    for path in _workflow_files():
        tree = parse_file(path)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for top in _import_top_levels(node):
                if top in _FORBIDDEN_IO_IMPORTS:
                    violations.append(
                        f"{relative_to_hubara(path)}:{node.lineno} imports `{top}` "
                        f"(R-DET forbids I/O libs in workflows)"
                    )
    assert not violations, (
        "R-DET violations — I/O libs imported by workflow modules:\n  "
        + "\n  ".join(violations)
    )


# ----------------------------------------------------------------------------
# Test #1b — call sites no-determinísticos en cualquier punto del workflow.
# ----------------------------------------------------------------------------

def test_workflows_dont_call_non_deterministic_apis() -> None:
    """Workflows no pueden llamar `time.time()`, `datetime.now()`, `uuid.uuid4()`,
    `random.*`, ni leer `os.environ`. Tampoco `open(...)` builtin.

    Las versiones determinísticas viven en `workflow.*`: `workflow.now()`,
    `workflow.uuid4()`, `workflow.sleep(...)`.
    """
    violations: list[str] = []
    for path in _workflow_files():
        tree = parse_file(path)
        rel = relative_to_hubara(path)
        for node in ast.walk(tree):
            # bare-name calls: open(...)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in _FORBIDDEN_BUILTIN_CALLS
            ):
                violations.append(
                    f"{rel}:{node.lineno} calls builtin `{node.func.id}(...)` "
                    f"(R-DET forbids filesystem I/O in workflows)"
                )
                continue
            # attribute access: time.time, datetime.now, os.environ, random.X
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                mod = node.value.id
                attr = node.attr
                if (mod, attr) in _FORBIDDEN_CALLS:
                    violations.append(
                        f"{rel}:{node.lineno} uses `{mod}.{attr}` "
                        f"(R-DET — use `workflow.{attr if attr in ('now','uuid4') else 'sleep/uuid4/now'}(...)` instead)"
                    )
                elif mod in _FORBIDDEN_MODULE_ANY_CALL:
                    violations.append(
                        f"{rel}:{node.lineno} uses `{mod}.{attr}` "
                        f"(R-DET forbids `{mod}.*` in workflows — fetch random data inside an activity)"
                    )
                elif mod == "os" and attr == "environ":
                    violations.append(
                        f"{rel}:{node.lineno} reads `os.environ` "
                        f"(R-DET — env values must be passed via the workflow input DTO)"
                    )
    assert not violations, (
        "R-DET violations — non-deterministic calls in workflow code:\n  "
        + "\n  ".join(violations)
    )


# ----------------------------------------------------------------------------
# Test #2 — repo imports están envueltos en `imports_passed_through`.
# ----------------------------------------------------------------------------

def test_workflows_wrap_repo_imports_in_imports_passed_through() -> None:
    """Todo import de `src.*` o `exoclaw_temporal.*` debe estar bajo
    `with workflow.unsafe.imports_passed_through():`.

    Sin el bloque, el sandbox de replay falla con errores opacos. Es
    convención no-negociable en DEHA.
    """
    wrapped_prefixes = (
        "src",
        "exoclaw_temporal",
    )
    violations: list[str] = []
    for path in _workflow_files():
        tree = parse_file(path)
        parents = _build_parent_map(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            full_targets: list[str] = []
            if isinstance(node, ast.Import):
                full_targets.extend(alias.name for alias in node.names)
            elif node.module is not None and node.level == 0:
                full_targets.append(node.module)
            if not any(t.startswith(wrapped_prefixes) for t in full_targets):
                continue
            if not _is_inside_unsafe_block(node, parents):
                joined = ", ".join(full_targets)
                violations.append(
                    f"{relative_to_hubara(path)}:{node.lineno} imports `{joined}` "
                    f"outside `with workflow.unsafe.imports_passed_through():`"
                )
    assert not violations, (
        "R-DET violations — repo imports in workflows must be wrapped:\n  "
        + "\n  ".join(violations)
    )
