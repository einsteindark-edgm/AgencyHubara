"""G1 · golden-replay del `build()` COMPILADO de blended-economics (G-DET).

El StateGraph (single-node que REUSA el `run()` puro, que compone merge-by-date +
blended-unit-economics + diagnose) produce el MISMO output que el `run()`. Skipea sin
langgraph; corre verde en el panel (`uv run`) y en CI.
"""
from __future__ import annotations

import pytest

pytest.importorskip("langgraph")

SEED = {
    "currency": "COP",
    "insights": [
        {"date": "2026-06-15", "spend_cop": 300000, "inline_link_clicks": 500, "conversations_started": 100},
    ],
    "sales": [
        {"date": "2026-06-15", "total_orders": 15, "total_revenue_cop": 450000},
    ],
}


def test_compiled_graph_matches_run() -> None:
    from graphs.blended_economics import build, run
    from tools.blended_unit_economics.impl import run as metrics
    from tools.diagnose.impl import run as diag
    from tools.merge_by_date.impl import run as merge

    tools = {"merge-by-date": merge, "blended-unit-economics": metrics, "diagnose": diag}
    expected = run(dict(SEED), tools=tools)
    out = build().invoke(SEED)

    for k in expected:
        assert out[k] == expected[k], f"{k}: {out.get(k)!r} != {expected[k]!r}"
