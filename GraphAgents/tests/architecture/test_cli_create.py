"""Rojo del generador del CLI (`create tool`). El scaffold debe NACER:
- con los 5 archivos del patrón de tool agnóstica (contrato + impl pura + adapters),
- contrato válido que certifica C2 a nivel TCK (T-IMPL · T-DUR · G-AGNOSTIC),
- y su golden ROJO por construcción (la impl stub levanta NotImplementedError),
para que el dev solo tenga que implementar `run` + completar el golden.

Se scaffoldea en un tmp_path (no contamina tools/ real); los checks son file-based
(load_tool lee el yaml, G-AGNOSTIC AST-parsea el impl.py por ruta) → no requiere
que el módulo sea importable.
"""
from __future__ import annotations

from sdk.scaffold import ScaffoldError, create_tool
from sdk.testkit.tool_checks import tool_level
from sdk.tool_model import load_tool


def test_create_tool_writes_certifiable_scaffold(tmp_path) -> None:
    paths = create_tool("demo-tool", tmp_path, description="demo de scaffold")

    tdir = tmp_path / "tools" / "demo_tool"
    assert (tdir / "tool.yaml").exists()
    assert (tdir / "impl.py").exists()
    assert (tdir / "adapters" / "langgraph.py").exists()
    assert (tdir / "adapters" / "agentspan.py").exists()
    assert (tmp_path / "tests" / "tools" / "test_demo_tool.py").exists()
    assert (tdir / "tool.yaml") in paths

    c = load_tool(tdir / "tool.yaml")
    assert c.id == "demo-tool"  # id kebab; el directorio/módulo es snake
    assert c.impl == "tools.demo_tool.impl:run"
    assert tool_level(c, tmp_path) == "C2"  # contrato válido + impl pura


def test_scaffold_is_red_by_construction(tmp_path) -> None:
    create_tool("demo-tool", tmp_path, description="x")
    impl_src = (tmp_path / "tools" / "demo_tool" / "impl.py").read_text(encoding="utf-8")
    # la impl nace levantando NotImplementedError → el golden de tests/tools falla
    # hasta que el dev la implemente (la presión de diseño de TDD, por construcción).
    assert "NotImplementedError" in impl_src


def test_create_tool_refuses_overwrite(tmp_path) -> None:
    create_tool("demo-tool", tmp_path, description="x")
    try:
        create_tool("demo-tool", tmp_path, description="x")
    except ScaffoldError:
        return
    raise AssertionError("create_tool debía rehusarse a sobrescribir un id existente")


def test_outward_tool_is_born_with_approval(tmp_path) -> None:
    # T-DUR: una tool outward DEBE nacer con approval_required=true, o no certifica.
    create_tool("demo-out", tmp_path, description="x", side_effect="outward")
    c = load_tool(tmp_path / "tools" / "demo_out" / "tool.yaml")
    assert c.side_effect == "outward"
    assert c.approval_required is True
    assert tool_level(c, tmp_path) == "C2"
