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
# `@activity.defn`: las del turno y la del egreso del workflow V2), la fachada
# del workflow (las reexporta) y el enrutador del workflow que registran los
# workers (lee el vault con `src.sdk.runtime`).
_TEMPORAL_OK = {"activities.py", "egress_activities.py", "facade.py", "routing.py"}


def _is_src_module(module: str) -> bool:
    base = SRC.parent.joinpath(*module.split("."))
    return base.with_suffix(".py").exists() or (base / "__init__.py").exists()


def _imports(path: Path, source: str | None = None) -> set[str]:
    """Los módulos que importa `path` (`source`: otro contenido para ese
    mismo archivo, para los controles negativos). Resuelve los imports
    relativos contra el paquete del archivo, y `from paquete import módulo`
    cuenta como el submódulo: ninguno de los dos se salta la frontera."""
    text = path.read_text(encoding="utf-8") if source is None else source
    tree = ast.parse(text, filename=str(path))
    package = path.relative_to(SRC.parent).with_suffix("").parts
    package = package if path.name == "__init__.py" else package[:-1]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = list(package[: len(package) - node.level + 1]) if node.level else []
            module = ".".join([*base, node.module] if node.module else base)
            if not module:
                continue
            for alias in node.names:
                submodule = f"{module}.{alias.name}"
                found.add(submodule if module.startswith("src.") and _is_src_module(submodule) else module)
    return found


def _engine_modules() -> list[Path]:
    return sorted(p for p in DECISIONS.rglob("*.py") if "__pycache__" not in p.parts)


@pytest.mark.parametrize("workflow_file", ["sales_session.py", "sales_session_v2.py"])
def test_the_sales_workflow_sees_only_the_contract_and_the_facade(workflow_file: str) -> None:
    """V1 y V2 (F4): el workflow solo ve el contrato y la fachada del motor."""
    engine_imports = {m for m in _imports(SALES / "workflows" / workflow_file) if m.startswith(PKG)}

    assert engine_imports <= {f"{PKG}.contracts", f"{PKG}.facade"}, engine_imports


@pytest.mark.parametrize(
    "line, reached",
    [
        ("from ..decisions.egress import Destinatario\n", f"{PKG}.egress"),
        ("from ..decisions import egress\n", f"{PKG}.egress"),
        ("from .. import decisions\n", PKG),
        ("from src.plugins.chats.agent.sales.decisions import capabilities\n", f"{PKG}.capabilities"),
    ],
)
def test_the_boundary_sees_relative_and_submodule_imports(line: str, reached: str) -> None:
    """Control negativo de la frontera: el V2 no puede alcanzar el motor con
    un import relativo ni importando un submódulo por su nombre."""
    found = _imports(SALES / "workflows" / "sales_session_v2.py", source=line)

    assert reached in found, found


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
