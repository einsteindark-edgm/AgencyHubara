"""Golden-replay (G-DET) de la capability `meta-insights`: fixture de insights →
output EXACTO. Corre PURO (sin langgraph/agentspan) — el determinismo vive en el
esqueleto, no en el runtime."""
from __future__ import annotations

import json
from pathlib import Path

from graphs.meta_insights import run
from sdk.connectorkit.ports import FixtureMetaInsights

GA = Path(__file__).resolve().parents[2]
INPUT = {"account_id": "act_1", "since": "2026-06-01", "until": "2026-06-18"}


def _sample() -> list[dict]:
    return json.loads((GA / "fixtures" / "meta_insights_sample.json").read_text(encoding="utf-8"))


def test_meta_insights_golden_replay() -> None:
    out = run(INPUT, ports={"meta_marketing_api": FixtureMetaInsights(_sample())})
    assert out == {
        "campaigns": [
            {"campaign_id": "c1", "spend": 100.0, "revenue": 300.0, "roas": 3.0},
            {"campaign_id": "c2", "spend": 50.0, "revenue": 50.0, "roas": 1.0},
        ],
        "total_spend": 150.0,
        "total_revenue": 350.0,
        "blended_roas": 2.3333,
    }


def test_es_idempotente() -> None:
    p = FixtureMetaInsights(_sample())
    assert run(INPUT, ports={"meta_marketing_api": p}) == run(INPUT, ports={"meta_marketing_api": p})
