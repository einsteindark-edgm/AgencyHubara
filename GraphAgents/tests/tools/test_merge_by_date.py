"""Per-tool TCK de `merge-by-date` — golden del join por fecha (portado de test_merge.py):
inner-join + las fechas sin match se SURFACEAN (no se dropean). Fecha duplicada = error."""
from __future__ import annotations

from pathlib import Path

import pytest

from sdk.testkit.tool_checks import run_tool_checks, tool_level
from sdk.tool_model import load_tool
from tools.merge_by_date.impl import run

GA = Path(__file__).resolve().parents[2]
TOOL = GA / "tools" / "merge_by_date" / "tool.yaml"

_INS = [
    {"date": "2026-06-01", "spend_cop": 1000, "inline_link_clicks": 10, "conversations_started": 5},
    {"date": "2026-06-02", "spend_cop": 2000, "inline_link_clicks": 20, "conversations_started": 8},
]
_SALES = [
    {"date": "2026-06-02", "total_orders": 3, "total_revenue_cop": 90000},
    {"date": "2026-06-03", "total_orders": 1, "total_revenue_cop": 30000},
]


def test_contrato_carga_y_certifica_C2() -> None:
    c = load_tool(TOOL)
    assert run_tool_checks(c, GA)["errors"] == []
    assert tool_level(c, GA) == "C2"


def test_inner_join_surfacea_lo_no_matcheado() -> None:
    out = run(payload={"insights": _INS, "sales": _SALES})
    assert out["days"] == [{
        "date": "2026-06-02", "spend_cop": 2000, "inline_link_clicks": 20,
        "conversations_started": 8, "total_orders": 3, "total_revenue_cop": 90000,
    }]
    assert out["meta_only"] == ["2026-06-01"]   # no se dropea en silencio
    assert out["sales_only"] == ["2026-06-03"]


def test_dias_ordenados_por_fecha() -> None:
    ins = [{"date": d, "spend_cop": 1, "inline_link_clicks": 1, "conversations_started": 1}
           for d in ("2026-06-03", "2026-06-01", "2026-06-02")]
    sal = [{"date": d, "total_orders": 1, "total_revenue_cop": 1}
           for d in ("2026-06-03", "2026-06-01", "2026-06-02")]
    out = run(payload={"insights": ins, "sales": sal})
    assert [d["date"] for d in out["days"]] == ["2026-06-01", "2026-06-02", "2026-06-03"]


def test_fecha_duplicada_explota() -> None:
    dup = [{"date": "2026-06-01", "spend_cop": 1, "inline_link_clicks": 1, "conversations_started": 1}] * 2
    with pytest.raises(ValueError):
        run(payload={"insights": dup, "sales": []})
