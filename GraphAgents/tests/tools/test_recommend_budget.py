"""Per-tool TCK de `recommend-budget`: contrato certifica + la impl es pura,
determinista (golden) e idempotente."""
from __future__ import annotations

from pathlib import Path

from sdk.testkit.tool_checks import run_tool_checks, tool_level
from sdk.tool_model import load_tool
from tools.recommend_budget.impl import run

GA = Path(__file__).resolve().parents[2]
TOOL = GA / "tools" / "recommend_budget" / "tool.yaml"


def test_contrato_carga_y_certifica_C2() -> None:
    c = load_tool(TOOL)
    assert run_tool_checks(c, GA)["errors"] == []
    assert tool_level(c, GA) == "C2"


def test_impl_es_determinista_golden() -> None:
    out = run(adsets=[{"id": "a", "roas": 2.0}, {"id": "b", "roas": 1.0}], total_budget=300.0)
    assert out == {"allocations": [{"id": "a", "budget": 200.0}, {"id": "b", "budget": 100.0}]}


def test_impl_idempotente() -> None:
    args = {"adsets": [{"id": "a", "roas": 1.5}], "total_budget": 100.0}
    assert run(**args) == run(**args)


def test_reparto_uniforme_sin_roas() -> None:
    out = run(adsets=[{"id": "a", "roas": 0}, {"id": "b", "roas": 0}], total_budget=100.0)
    assert out == {"allocations": [{"id": "a", "budget": 50.0}, {"id": "b", "budget": 50.0}]}
