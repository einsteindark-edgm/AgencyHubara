"""G1 · golden-replay del `build()` COMPILADO de ctwa-insights (G-DET).

El StateGraph (single-node que REUSA el `run()` puro) debe producir el MISMO output que
el `run()`: prueba que el grafo compila, que la `State` propaga las claves de salida (un
TypedDict al que le falta una clave la DROPEA silenciosamente — este replay lo caza), y
que AgentSpan puede correrlo por passthrough. Skipea sin langgraph (loop local python3
desnudo); corre verde en el panel (`uv run`) y en CI.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

GA = Path(__file__).resolve().parents[2]
FIX = GA / "fixtures" / "meta_insights_campaigns.json"


def test_compiled_graph_matches_run() -> None:
    from graphs.ctwa_insights import build, run
    from tools.meta_ads_insights.impl import run as parse

    seed = {"payload": json.loads(FIX.read_text(encoding="utf-8"))}
    expected = run(dict(seed), tools={"meta-ads-insights": parse})
    out = build().invoke(seed)

    for k in expected:  # el grafo compilado == la función pura (mismas claves de salida)
        assert out[k] == expected[k], f"{k}: {out.get(k)!r} != {expected[k]!r}"
