"""Gate de arquitectura de las tools del catálogo: toda tool certifica, y los
casos NEGATIVOS prueban que T-DUR y G-AGNOSTIC muerden (un gate que nunca falla
es un gate roto)."""
from __future__ import annotations

from pathlib import Path

import pytest

from sdk.testkit.tool_checks import check_impl_is_agnostic, run_tool_checks, tool_level
from sdk.tool_model import ToolContract, load_tool

GA = Path(__file__).resolve().parents[2]
TOOLS = sorted((GA / "tools").glob("*/tool.yaml"))


def test_hay_tools_en_el_catalogo() -> None:
    assert TOOLS, "no hay tools/*/tool.yaml"


@pytest.mark.parametrize("path", TOOLS, ids=lambda p: p.parent.name)
def test_cada_tool_certifica_al_menos_C1(path: Path) -> None:
    c = load_tool(path)
    assert tool_level(c, GA) in {"C1", "C2", "C3"}, run_tool_checks(c, GA)


def test_tck_caza_outward_sin_approval() -> None:
    c = ToolContract(id="x", version="1.0.0", side_effect="outward", approval_required=False, impl="m:f")
    errs = run_tool_checks(c)["errors"]
    assert any("T-DUR" in e for e in errs)


def test_tck_caza_impl_que_importa_runtime(tmp_path: Path) -> None:
    (tmp_path / "tools" / "bad").mkdir(parents=True)
    (tmp_path / "tools" / "bad" / "impl.py").write_text(
        "import langgraph\ndef run():\n    return 1\n", encoding="utf-8"
    )
    c = ToolContract(id="bad", version="1.0.0", impl="tools.bad.impl:run")
    errs = check_impl_is_agnostic(c, tmp_path)
    assert any("G-AGNOSTIC" in e for e in errs)
