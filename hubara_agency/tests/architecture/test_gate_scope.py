"""Meta-gate — los gates AST de las R-rules no pueden pasar en vacío.

Por qué existe
--------------
Un gate AST que itera un glob sin matches pasa en verde sin auditar nada. Pasó
hasta 2026-09-23: R-DET, R-HEARTBEAT, R-STATELESS y la regla de naming de tools
iteraban el alias `AGENT_*_GLOB = AGENT_*_GLOBS[0]` (`src/*/workflows/*.py`, 0
archivos desde que los agentes viven en `src/plugins/`), y ni las tuplas
completas cubrían el layout de agente único (`agent/workflows/`), las activities
definidas en `__init__.py` ni los `@activity.defn` fuera de `activities/`.
Ver `docs/adr/2026-09-23-arch-gates-non-vacuous-scope.md`.

Qué asserta
-----------
Para cada gate: (a) el set de archivos que escanea no es vacío y (b) cubre el
ground truth de su rol. El ground truth sale del código, NO de los globs ni de
los helpers de `conftest.py` (el auditor no reusa el código que audita):
  * workflows / activities: módulos directos de todo directorio `workflows/` /
    `activities/` bajo `src/` (salvo `__init__.py`, shim de re-export) + todo
    módulo que define `@workflow.defn` / `@activity.defn`, viva donde viva;
  * tools: todo módulo que declara una subclase de `ToolBase`;
  * contracts: todo `contracts.py` de agente (`src/<pkg>/` o bajo un `agent/`).

Tests:
  #M1 — `test_gate_scans_a_non_empty_file_set`
  #M2 — `test_gate_covers_every_module_of_its_role`
  #M3 — `test_role_ground_truth_is_not_empty` (el propio meta-gate no pasa en vacío)
"""
from __future__ import annotations

import ast
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

import pytest

from tests.architecture import (
    test_anti_patterns,
    test_r_det,
    test_r_heartbeat,
    test_r_json,
    test_r_stateless,
)

_HUB_ROOT: Path = Path(__file__).resolve().parents[2]
_SRC: Path = _HUB_ROOT / "src"

# gate → (collector que el gate itera, rol que debe cubrir)
_GATES: dict[str, tuple[Callable[[], list[Path]], str]] = {
    "R-DET": (test_r_det._workflow_files, "workflows"),
    "R-HEARTBEAT": (test_r_heartbeat._activity_modules, "activities"),
    "R-STATELESS": (test_r_stateless._activity_files, "activities"),
    "R-JSON": (test_r_json._contracts_files, "contracts"),
    "tool-naming": (test_anti_patterns._tool_files, "tools"),
}


@lru_cache(maxsize=1)
def _src_modules() -> tuple[Path, ...]:
    return tuple(sorted(p for p in _SRC.rglob("*.py") if "__pycache__" not in p.parts))


def _base_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _entrypoints(tree: ast.Module) -> frozenset[str]:
    """Qué define el módulo: "workflow"/"activity" (`@<x>.defn`) y/o "tool"."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(_base_name(b) == "ToolBase" for b in node.bases):
            found.add("tool")
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for dec in node.decorator_list:
            fn = dec.func if isinstance(dec, ast.Call) else dec
            if (
                isinstance(fn, ast.Attribute)
                and fn.attr == "defn"
                and isinstance(fn.value, ast.Name)
                and fn.value.id in ("workflow", "activity")
            ):
                found.add(fn.value.id)
    return frozenset(found)


@lru_cache(maxsize=1)
def _entrypoints_by_module() -> dict[Path, frozenset[str]]:
    return {
        p: _entrypoints(ast.parse(p.read_text(encoding="utf-8"), filename=str(p)))
        for p in _src_modules()
    }


def _defining(entrypoint: str) -> set[Path]:
    return {p for p, found in _entrypoints_by_module().items() if entrypoint in found}


def _in_role_dir(dirname: str) -> set[Path]:
    return {p for p in _src_modules() if p.parent.name == dirname and p.name != "__init__.py"}


def _agent_contracts() -> set[Path]:
    return {
        p
        for p in _src_modules()
        if p.name == "contracts.py"
        and (p.parent.parent == _SRC or "agent" in p.relative_to(_SRC).parts)
    }


def _ground_truth(role: str) -> set[Path]:
    if role == "workflows":
        return _in_role_dir("workflows") | _defining("workflow")
    if role == "activities":
        return _in_role_dir("activities") | _defining("activity")
    if role == "tools":
        return _defining("tool")
    if role == "contracts":
        return _agent_contracts()
    raise ValueError(f"rol desconocido: {role}")


def _rel(path: Path) -> str:
    return path.relative_to(_HUB_ROOT).as_posix()


@pytest.mark.parametrize("gate", sorted(_GATES))
def test_gate_scans_a_non_empty_file_set(gate: str) -> None:
    collect, role = _GATES[gate]
    assert collect(), (
        f"{gate} no escanea ningún archivo ({role}) — pasa en verde sin auditar nada. "
        f"Revisar los globs de conftest.py contra el layout real de src/."
    )


@pytest.mark.parametrize("gate", sorted(_GATES))
def test_gate_covers_every_module_of_its_role(gate: str) -> None:
    collect, role = _GATES[gate]
    scanned = {p.resolve() for p in collect()}
    missing = sorted(_rel(p) for p in _ground_truth(role) - scanned)
    assert not missing, (
        f"{gate} no audita {len(missing)} módulo(s) de su rol ({role}):\n  "
        + "\n  ".join(missing)
        + "\n\nEl gate pasa en verde sin mirarlos. Si es un layout nuevo, agregar su "
        "patrón a la tupla de globs de conftest.py o mover el código al layout "
        "estándar — nunca excluirlo en silencio. "
        "Ver docs/adr/2026-09-23-arch-gates-non-vacuous-scope.md."
    )


@pytest.mark.parametrize("role", ["activities", "contracts", "tools", "workflows"])
def test_role_ground_truth_is_not_empty(role: str) -> None:
    assert _ground_truth(role), (
        f"El ground truth de '{role}' salió vacío — el meta-gate cubriría un set "
        f"vacío y pasaría en vacío. ¿Cambió el layout de src/ o el nombre del entrypoint?"
    )
