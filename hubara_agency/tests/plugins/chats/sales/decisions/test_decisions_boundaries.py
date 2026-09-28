"""Fronteras del motor de decisiones (diseño v2 §03, regla 5).

Cambiar el motor (una pregunta nueva, otra política) no puede tocar a sus
consumidores. Por eso:

* el workflow de ventas solo importa del motor `contracts` y `facade`;
* las tools solo importarán `guards` (F6);
* el motor no importa ningún workflow, y su núcleo (todo menos las
  activities y la fachada) no importa Temporal: así las tools pueden usarlo
  sin arrastrar el SDK de Temporal (contrato `tools-no-temporal`).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[5] / "src"
SALES = SRC / "plugins" / "chats" / "agent" / "sales"
DECISIONS = SALES / "decisions"
PKG = "src.plugins.chats.agent.sales.decisions"
# Módulos del motor que PUEDEN importar Temporal: las activities (llevan
# `@activity.defn`) y la fachada del workflow (las reexporta).
_TEMPORAL_OK = {"activities.py", "facade.py"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


def _engine_modules() -> list[Path]:
    return sorted(p for p in DECISIONS.rglob("*.py") if "__pycache__" not in p.parts)


def test_the_sales_workflow_sees_only_the_contract_and_the_facade() -> None:
    engine_imports = {m for m in _imports(SALES / "workflows" / "sales_session.py") if m.startswith(PKG)}

    assert engine_imports <= {f"{PKG}.contracts", f"{PKG}.facade"}, engine_imports


def test_tools_only_reach_the_engine_through_its_guards() -> None:
    for tool in sorted((SALES / "tools").glob("*.py")):
        engine_imports = {m for m in _imports(tool) if m.startswith(PKG)}
        assert engine_imports <= {f"{PKG}.guards"}, (tool.name, engine_imports)


@pytest.mark.parametrize("module", _engine_modules(), ids=lambda p: str(p.relative_to(DECISIONS)))
def test_the_engine_never_imports_a_workflow(module: Path) -> None:
    bad = {m for m in _imports(module) if ".workflows" in m or m == "temporalio.workflow"}

    assert not bad, bad


@pytest.mark.parametrize(
    "module",
    [p for p in _engine_modules() if p.name not in _TEMPORAL_OK],
    ids=lambda p: str(p.relative_to(DECISIONS)),
)
def test_the_engine_core_is_temporal_free(module: Path) -> None:
    """Núcleo sin Temporal (ni `src.sdk.runtime`, que lo arrastra): las
    tools lo usarán para revisar datos sin romper `tools-no-temporal`."""
    bad = {m for m in _imports(module) if m.startswith("temporalio") or m == "src.sdk.runtime"}

    assert not bad, bad
