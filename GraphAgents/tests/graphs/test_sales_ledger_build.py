"""G1 · golden-replay del `build()` COMPILADO de sales-ledger (G-DET).

Single-node que REUSA el `run()` puro → produce el MISMO output que el `run()`. Skipea sin
langgraph. (sales-ledger es capability del catálogo: su `build()` debe existir para correrla
standalone en AgentSpan, igual que los otros 4 extractores/analyzers — ver cert-review.)
"""
from __future__ import annotations

import pytest

pytest.importorskip("langgraph")

SEED = {"payload": {"sales": [
    {"date": "2026-06-15", "total_orders": 12, "total_revenue": 600000},
    {"date": "2026-06-16", "total_orders": 4, "total_revenue": 150000},
]}}


def test_compiled_graph_matches_run() -> None:
    from graphs.sales_ledger import build, run
    from tools.parse_manual_sales.impl import run as parse

    expected = run(dict(SEED), tools={"parse-manual-sales": parse})
    out = build().invoke(SEED)

    for k in expected:
        assert out[k] == expected[k], f"{k}: {out.get(k)!r} != {expected[k]!r}"
