"""Golden-replay de `blended-economics`: inyecta las 3 tools y asierta el blend completo
compuesto (merge + métricas + diagnóstico), con fechas sin match surfaceadas.

06-15 matchea (insight + venta): drop 0.8, mer 1.5 → rotate_creative.
06-16 solo-Meta · 06-17 solo-Ventas → unmatched (no se dropean).
"""
from __future__ import annotations

from graphs.blended_economics import run
from tools.blended_unit_economics.impl import run as blended_unit_economics
from tools.diagnose.impl import run as diagnose
from tools.merge_by_date.impl import run as merge_by_date

TOOLS = {
    "merge-by-date": merge_by_date,
    "blended-unit-economics": blended_unit_economics,
    "diagnose": diagnose,
}

INSIGHTS = [
    {"date": "2026-06-15", "spend_cop": 300000, "inline_link_clicks": 500, "conversations_started": 100},
    {"date": "2026-06-16", "spend_cop": 100000, "inline_link_clicks": 100, "conversations_started": 90},
]
SALES = [
    {"date": "2026-06-15", "total_orders": 15, "total_revenue_cop": 450000},
    {"date": "2026-06-17", "total_orders": 5, "total_revenue_cop": 100000},
]


def test_golden_blend_compuesto() -> None:
    out = run({"insights": INSIGHTS, "sales": SALES}, tools=TOOLS)

    assert out["days"] == [{
        "date": "2026-06-15",
        "spend_cop": 300000, "inline_link_clicks": 500, "conversations_started": 100,
        "total_orders": 15, "total_revenue_cop": 450000,
        "metrics": {
            "drop_off_rate": "0.8", "cost_per_conversation_cop": "3000", "mer": "1.5",
            "global_cpa_cop": "20000", "global_win_rate": "0.15",
        },
        "diagnosis": {"high_friction": True, "poor_profitability": True, "recommendation": "rotate_creative"},
    }]
    # el periodo (1 día en común) replica las métricas del día
    assert out["period"]["metrics"]["mer"] == "1.5"
    assert out["period"]["diagnosis"]["recommendation"] == "rotate_creative"
    # fechas sin match SURFACEADAS, no dropeadas
    assert out["unmatched"] == {"meta_only": ["2026-06-16"], "sales_only": ["2026-06-17"]}


def test_rechaza_currency_no_cop() -> None:
    # MF-5: una cuenta en otra moneda no se blendea en silencio — se rehúsa (COP-only).
    import pytest
    with pytest.raises(ValueError):
        run({"currency": "USD", "insights": INSIGHTS, "sales": SALES}, tools=TOOLS)
