"""Spinal coherence — worker.py is in sync with its tools / workflows / activities.

After the parallel implementer/merger pipeline runs, the worker.py of each agent
may end up with stale lists (workflow not registered, tool not extended). These
tests act as a post-merge consistency check.

Tests:
  #13 — every `@workflow.defn` in `src/<agent>/workflows/` and `@activity.defn`
        in `src/<agent>/activities/` is registered in `src/<agent>/worker.py`.
  #14 — every `class XTool(ToolBase)` in `src/<agent>/tools/` has a matching
        `register_tool_extension(...)` call in `src/<agent>/worker.py`.
  #16 — every package and module imports cleanly (full smoke).
"""
from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

from tests.architecture.conftest import (
    DEHA_AGENTS,
    SRC_ROOT,
    agent_paths,
    parse_file,
    relative_to_hubara,
)


def _agent_worker_path(agent: str) -> Path:
    return agent_paths(agent)["worker"]


def _workflow_class_names_for_agent(agent: str) -> list[tuple[str, Path]]:
    """[(class_name, file_path)] de todos los @workflow.defn del agente."""
    agent_root = agent_paths(agent)["root"] / "workflows"
    if not agent_root.exists():
        return []
    out: list[tuple[str, Path]] = []
    for path in agent_root.rglob("*.py"):
        if path.name == "__init__.py":
            continue
        tree = parse_file(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for dec in node.decorator_list:
                func = dec.func if isinstance(dec, ast.Call) else dec
                attr = getattr(func, "attr", None)
                if attr == "defn":
                    out.append((node.name, path))
                    break
    return out


def _activity_function_names_for_agent(agent: str) -> list[tuple[str, Path]]:
    """[(function_name, file_path)] de todos los @activity.defn del agente."""
    agent_root = agent_paths(agent)["root"] / "activities"
    if not agent_root.exists():
        return []
    out: list[tuple[str, Path]] = []
    for path in agent_root.rglob("*.py"):
        if path.name == "__init__.py":
            continue
        tree = parse_file(path)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                func = dec.func if isinstance(dec, ast.Call) else dec
                attr = getattr(func, "attr", None)
                if attr == "defn":
                    out.append((node.name, path))
                    break
    return out


def _tool_class_names_for_agent(agent: str) -> list[tuple[str, Path]]:
    """[(class_name, file_path)] de todas las subclases de ToolBase del agente."""
    agent_root = agent_paths(agent)["root"] / "tools"
    if not agent_root.exists():
        return []
    out: list[tuple[str, Path]] = []
    for path in agent_root.rglob("*.py"):
        if path.name == "__init__.py":
            continue
        tree = parse_file(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names: list[str] = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    base_names.append(base.id)
                elif isinstance(base, ast.Attribute):
                    base_names.append(base.attr)
            if "ToolBase" in base_names:
                out.append((node.name, path))
    return out


def _worker_text(agent: str) -> str:
    p = _agent_worker_path(agent)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


# ----------------------------------------------------------------------------
# Test #13 — workflows + activities están registradas en worker.py.
# ----------------------------------------------------------------------------

@pytest.mark.parametrize("agent", DEHA_AGENTS)
def test_worker_registers_every_workflow_and_activity(agent: str) -> None:
    """worker.py debe listar cada @workflow.defn y @activity.defn del agente.

    Comprobamos por sustring textual del nombre — más resiliente a refactors de
    estilo (lista vs. tuple, kwargs, etc.) que parsear el AST del worker.
    """
    worker_src = _worker_text(agent)
    worker_path_rel = relative_to_hubara(_agent_worker_path(agent))
    assert worker_src, f"{worker_path_rel} is missing"

    missing: list[str] = []
    for class_name, path in _workflow_class_names_for_agent(agent):
        if class_name not in worker_src:
            missing.append(
                f"workflow `{class_name}` defined in {relative_to_hubara(path)} "
                f"is not referenced by {worker_path_rel}"
            )
    for fn_name, path in _activity_function_names_for_agent(agent):
        if fn_name not in worker_src:
            missing.append(
                f"activity `{fn_name}` defined in {relative_to_hubara(path)} "
                f"is not referenced by {worker_path_rel}"
            )
    assert not missing, (
        f"Spinal coherence — worker.py drift for {agent}:\n  " + "\n  ".join(missing)
    )


# ----------------------------------------------------------------------------
# Test #14 — cada ToolBase tiene un register_tool_extension en worker.py.
# ----------------------------------------------------------------------------

@pytest.mark.parametrize("agent", DEHA_AGENTS)
def test_worker_registers_every_tool(agent: str) -> None:
    """Cada subclase de ToolBase definida bajo `<agent_root>/tools/` debe aparecer
    como argumento de algún `register_tool_extension(...)` en el worker del agente.

    Agentes que aún no tienen tools (catalog.sync, chats.remarketing) hacen
    short-circuit con un `pass` implícito.
    """
    tool_classes = _tool_class_names_for_agent(agent)
    if not tool_classes:
        return
    worker_src = _worker_text(agent)
    worker_path_rel = relative_to_hubara(_agent_worker_path(agent))
    missing: list[str] = []
    for class_name, path in tool_classes:
        if f"{class_name}(" not in worker_src:
            missing.append(
                f"tool `{class_name}` defined in {relative_to_hubara(path)} "
                f"is not registered in {worker_path_rel} via register_tool_extension(...)"
            )
    assert not missing, (
        f"Spinal coherence — tool not registered in worker.py for {agent}:\n  "
        + "\n  ".join(missing)
    )


# ----------------------------------------------------------------------------
# Test #16 — full import smoke (extiende el test_imports.py existente).
# ----------------------------------------------------------------------------

def _iter_all_src_modules() -> list[str]:
    """Walk de paquetes bajo src/, devolviendo nombres dotted."""
    import src  # type: ignore  # local package

    names: list[str] = []
    for module_info in pkgutil.walk_packages(src.__path__, prefix="src."):
        # Excluimos los módulos HTTP-edge bajo plugins/<id>/api (FastAPI, no DEHA).
        # PR2 movió `src.dashboard.*` → `src.plugins.chats.api.*`; mantenemos
        # la misma exclusión semántica.
        if module_info.name.startswith("src.plugins.chats.api"):
            continue
        if module_info.name.startswith("src.tests"):
            continue
        names.append(module_info.name)
    return names


def test_every_src_module_imports_cleanly() -> None:
    """Todos los módulos bajo src/ (excepto dashboard/) deben importarse sin error.

    Detecta roturas de path tras refactors (movimientos, renames) — el smoke
    test legacy en tests/test_imports.py cubre solo 5 paths concretos; esto
    cubre el universo completo.
    """
    failed: list[str] = []
    for name in _iter_all_src_modules():
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 — captura amplia es el punto
            failed.append(f"{name}: {type(exc).__name__}: {exc}")
    assert not failed, (
        "Spinal coherence — modules fail to import:\n  " + "\n  ".join(failed)
    )
