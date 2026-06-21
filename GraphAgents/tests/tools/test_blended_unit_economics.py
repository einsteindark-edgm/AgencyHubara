"""Per-tool TCK de `blended-unit-economics` — golden de las 5 métricas, valores
hand-computados (la garantía anti-alucinación, portada de test_metrics.py del motor).
Decimal exacto como string; denominador 0 → None."""
from __future__ import annotations

from pathlib import Path

from sdk.testkit.tool_checks import run_tool_checks, tool_level
from sdk.tool_model import load_tool
from tools.blended_unit_economics.impl import run

GA = Path(__file__).resolve().parents[2]
TOOL = GA / "tools" / "blended_unit_economics" / "tool.yaml"


def test_contrato_carga_y_certifica_C2() -> None:
    c = load_tool(TOOL)
    assert run_tool_checks(c, GA)["errors"] == []
    assert tool_level(c, GA) == "C2"


def test_golden_cinco_metricas_exactas() -> None:
    # 1 - 100/500 = 0.8 ; 300000/100 = 3000 ; 450000/300000 = 1.5 ;
    # 300000/15 = 20000 ; 15/100 = 0.15
    out = run(payload={
        "spend_cop": 300000, "inline_link_clicks": 500, "conversations_started": 100,
        "total_orders": 15, "total_revenue_cop": 450000,
    })
    assert out == {
        "drop_off_rate": "0.8",
        "cost_per_conversation_cop": "3000",
        "mer": "1.5",
        "global_cpa_cop": "20000",
        "global_win_rate": "0.15",
    }


def test_denominador_cero_da_none_pero_mer_cero_es_real() -> None:
    # sin clicks/conversaciones/órdenes → None; PERO revenue 0 sobre spend real = 0 REAL.
    out = run(payload={
        "spend_cop": 100000, "inline_link_clicks": 0, "conversations_started": 0,
        "total_orders": 0, "total_revenue_cop": 0,
    })
    assert out == {
        "drop_off_rate": None,
        "cost_per_conversation_cop": None,
        "mer": "0",
        "global_cpa_cop": None,
        "global_win_rate": None,
    }


def test_idempotente() -> None:
    p = {"spend_cop": 100000, "inline_link_clicks": 100, "conversations_started": 80,
         "total_orders": 40, "total_revenue_cop": 300000}
    assert run(payload=p) == run(payload=p)
