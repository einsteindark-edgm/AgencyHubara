"""G1 · golden-replay del `build()` COMPILADO de ctwa-scorecard (G-DET).

Single-node que REUSA el `run()` puro → produce el MISMO scorecard que el `run()` sobre el
caso real Halloween. Skipea sin langgraph."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

GA = Path(__file__).resolve().parents[2]
SEED = {"breakdown": json.loads((GA / "fixtures" / "halloween_breakdown.json").read_text(encoding="utf-8"))}


def test_compiled_graph_matches_run() -> None:
    from graphs.ctwa_scorecard import build, run
    from tools.entity_economics.impl import run as entity_economics

    expected = run(dict(SEED), tools={"entity-economics": entity_economics})
    out = build().invoke(SEED)

    assert out["scorecard"] == expected["scorecard"]
    assert out["scorecard"]["campaign"]["verdict"]["action"] == "hold_budget"
