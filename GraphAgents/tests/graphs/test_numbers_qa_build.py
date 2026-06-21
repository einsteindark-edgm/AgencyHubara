"""G1 · golden-replay del `build()` COMPILADO de numbers-qa (G-DET).

El StateGraph (single-node que REUSA el `run()` puro) produce el MISMO output que el
`run()`. El seed sale de un `blended-economics` real (métricas que reconcilian byte-for-byte
→ passed=True), no de números a mano. Skipea sin langgraph.
"""
from __future__ import annotations

import pytest

pytest.importorskip("langgraph")

BLEND_SEED = {
    "currency": "COP",
    "insights": [{"date": "2026-06-15", "spend_cop": 300000, "inline_link_clicks": 500, "conversations_started": 100}],
    "sales": [{"date": "2026-06-15", "total_orders": 15, "total_revenue_cop": 450000}],
}


def test_compiled_graph_matches_run() -> None:
    from graphs.blended_economics import run as blend_run
    from graphs.numbers_qa import build, run
    from tools.blended_unit_economics.impl import run as metrics
    from tools.diagnose.impl import run as diag
    from tools.merge_by_date.impl import run as merge

    blended = blend_run(dict(BLEND_SEED), tools={"merge-by-date": merge, "blended-unit-economics": metrics, "diagnose": diag})
    seed = {"days": blended["days"], "period": blended["period"]}
    expected = run(dict(seed), tools={"blended-unit-economics": metrics})
    out = build().invoke(seed)

    assert expected["passed"] is True  # data real reconcilia
    for k in expected:
        assert out[k] == expected[k], f"{k}: {out.get(k)!r} != {expected[k]!r}"
