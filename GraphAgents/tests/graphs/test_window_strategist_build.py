"""G1 · golden-replay del `build()` COMPILADO de window-strategist (G-DET).

El StateGraph (ingest→classify→plan→dispatch, cadena lineal L-15) debe producir
el MISMO resultado que el `run()` puro — la lógica vive UNA vez (L-11); este
replay caza el drift run↔build y claves dropeadas por el TypedDict. Skipea sin
langgraph (loop local python3 desnudo); corre verde en el panel y en CI.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

GA = Path(__file__).resolve().parents[2]
FIX = GA / "fixtures" / "window_strategist_snapshot.json"


def test_compiled_graph_matches_run() -> None:
    from graphs.window_strategist import build, run
    from tools.parse_conversations.impl import run as classify

    seed = {"payload": json.loads(FIX.read_text(encoding="utf-8"))}
    expected = run(dict(seed), tools={"parse-conversations": classify})
    out = build().invoke(seed)

    assert out["result"] == expected
