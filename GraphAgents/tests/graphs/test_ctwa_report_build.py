"""G1 · golden-replay del `build()` COMPILADO de ctwa-report (G-DET).

El StateGraph (single-node que REUSA el `run()` puro — el render determinista; el nodo LLM
narrativo es G1.x) produce el MISMO markdown/verdict que el `run()`. El seed sale de un
`blended-economics` real + el embudo por-campaña. Skipea sin langgraph.
"""
from __future__ import annotations

import pytest

pytest.importorskip("langgraph")

BLEND_SEED = {
    "currency": "COP",
    "insights": [{"date": "2026-06-15", "spend_cop": 300000, "inline_link_clicks": 500, "conversations_started": 100}],
    "sales": [{"date": "2026-06-15", "total_orders": 15, "total_revenue_cop": 450000}],
}
CAMPAIGNS = [
    {"campaign_id": "120243118818600317", "campaign_name": "Día del padre 2026 - mundia",
     "objective": "OUTCOME_SALES", "spend_cop": 239433, "link_clicks": 446,
     "conversations": 120, "conversation_source": "insights", "is_messaging": True},
]


def test_compiled_graph_matches_run() -> None:
    from graphs.blended_economics import run as blend_run
    from graphs.ctwa_report import build, run
    from tools.blended_unit_economics.impl import run as metrics
    from tools.diagnose.impl import run as diag
    from tools.merge_by_date.impl import run as merge

    blended = blend_run(dict(BLEND_SEED), tools={"merge-by-date": merge, "blended-unit-economics": metrics, "diagnose": diag})
    seed = {
        "days": blended["days"], "period": blended["period"], "unmatched": blended["unmatched"],
        "qa_passed": True, "campaigns": CAMPAIGNS,
    }
    expected = run(dict(seed))  # el reporter no usa tools
    out = build().invoke(seed)

    assert "Embudo por campaña" in out["markdown"]  # el embudo se renderiza vía el grafo
    for k in expected:
        assert out[k] == expected[k], f"{k}: {out.get(k)!r} != {expected[k]!r}"
