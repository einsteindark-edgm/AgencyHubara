"""G1 · golden-replay del `build()` COMPILADO de ctwa-campaign-funnel (G-DET).

A diferencia de los otros golden de `tests/graphs/` (que replayean el `run()` puro),
este replaye el `CompiledStateGraph` real vía `langgraph` — SIN server de AgentSpan.
Prueba que el StateGraph (un task por tool: parse-entities → parse-insights → complement)
produce EXACTAMENTE el mismo embudo que el `run()` puro: el esqueleto del grafo es
determinista (G-DET), así AgentSpan lo corre por el path passthrough como tasks durables.

Skipea si no hay langgraph (el loop local con python3 desnudo); corre verde en el panel
(`uv run`, con langgraph) y en CI.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

GA = Path(__file__).resolve().parents[2]
ENT_FIX = GA / "fixtures" / "mcp_ad_entities.json"
INS_FIX = GA / "fixtures" / "meta_insights_campaigns.json"


def _seed() -> dict:
    return {
        "entities_payload": json.loads(ENT_FIX.read_text(encoding="utf-8")),
        "insights_payload": json.loads(INS_FIX.read_text(encoding="utf-8")),
    }


def test_compiled_graph_replays_el_embudo_complementado() -> None:
    from graphs.ctwa_campaign_funnel import build

    graph = build()
    out = graph.invoke(_seed())

    campaigns = {c["campaign_id"]: c for c in out["campaigns"]}
    assert set(campaigns) == {"120238728477970317", "120243118818600317", "120240351877200317"}

    # EL COMPLEMENTO sobrevive la compilación a tasks: "Día del padre" (entities=0)
    # recupera su conversación de /insights y se reclasifica como messaging.
    padre = campaigns["120243118818600317"]
    assert padre["conversations"] == 120
    assert padre["conversation_source"] == "insights"
    assert padre["is_messaging"] is True

    # paridad con el run() puro: Duo zodiacal suma 70 (40+30) de /insights.
    assert campaigns["120238728477970317"]["conversations"] == 70


def test_compiled_graph_es_estable_entre_corridas() -> None:
    # G-DET: el grafo es puro → dos invocaciones del mismo seed dan el mismo output
    # (sin esto no se puede replayear/recuperar de forma durable).
    from graphs.ctwa_campaign_funnel import build

    a = build().invoke(_seed())
    b = build().invoke(_seed())
    assert a["campaigns"] == b["campaigns"]
