"""G1 · smoke de integración del runtime REAL de AgentSpan.

Corre SOLO si hay langgraph/agentspan instalados Y un server de AgentSpan
alcanzable (`AGENTSPAN_SERVER_URL` o `localhost:6767`); si no, se SKIPea — así el
loop local (python3, sin langgraph) y CI sin server siguen verdes.

En el container (con el server arriba):
    docker compose run --rm --no-deps graphagents \\
        /opt/venv/bin/python -m pytest tests/integration/test_agentspan_runtime.py -q

Asierta el ciclo completo: `build()` → `AgentSpanRuntime().run()` → `Execution`
COMPLETED con el output real de la capability (passthrough desempaquetado) + un
`execution-id` de Conductor (el que aparece en la UI de :6767).
"""
from __future__ import annotations

import os
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("agentspan")
pytest.importorskip("langgraph")

ROOT_PATH = Path(__file__).resolve().parents[2]  # .../GraphAgents


def _server_root() -> str:
    url = os.environ.get("AGENTSPAN_SERVER_URL", "http://localhost:6767")
    return url.rstrip("/").removesuffix("/api")


def _server_up() -> bool:
    try:
        urllib.request.urlopen(_server_root(), timeout=4)
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _server_up(), reason="no hay server AgentSpan alcanzable")


def test_greeter_runs_on_agentspan_and_unwraps_output():
    from graphs.greeter import build
    from sdk.runtime import AgentSpanRuntime

    ex = AgentSpanRuntime().run(build(), {"name": "ada"})

    assert ex.status == "completed"
    assert ex.id  # execution-id de Conductor (visible en la UI de :6767)
    # el output real de la capability, desempaquetado del passthrough {'result': '<json>'}
    assert ex.output.get("greeting") == "hola, ada"


def test_ctwa_campaign_funnel_runs_durable_and_keeps_the_complement():
    """G1 · el embudo por-campaña corre como StateGraph durable en AgentSpan (build() →
    AgentRuntime, passthrough = el grafo entero como una task durable con su execution-id
    de Conductor; ver L-11). Lo que prueba el smoke: el COMPLEMENTO sobrevive el round-trip
    durable — "Día del padre" (entities=0) recupera 120 conversaciones de /insights actions."""
    import json

    from graphs.ctwa_campaign_funnel import build
    from sdk.runtime import AgentSpanRuntime

    entities = json.loads((ROOT_PATH / "fixtures" / "mcp_ad_entities.json").read_text(encoding="utf-8"))
    insights = json.loads((ROOT_PATH / "fixtures" / "meta_insights_campaigns.json").read_text(encoding="utf-8"))

    ex = AgentSpanRuntime().run(build(), {"entities_payload": entities, "insights_payload": insights})

    assert ex.status == "completed"
    assert ex.id  # execution-id de Conductor (visible en la UI de :6767)
    campaigns = {c["campaign_id"]: c for c in ex.output["campaigns"]}
    padre = campaigns["120243118818600317"]
    assert padre["conversations"] == 120
    assert padre["conversation_source"] == "insights"
    assert padre["is_messaging"] is True


def test_explorer_run_endpoint_uses_agentspan():
    """El botón 'correr' del explorer con runtime=agentspan → ejecución durable
    (mismo `/api/run` que dispara la UI de :8900)."""
    from viewer.server import api_route

    status, payload = api_route(
        "POST",
        "/api/run",
        {},
        {"agent": "greeter", "input": {"name": "eva"}, "runtime": "agentspan"},
        ga_root=ROOT_PATH,
    )
    assert status == 200
    assert payload["status"] == "completed"
    assert payload["runtime"] == "agentspan"
    assert payload["id"]  # execution-id de Conductor → aparece en :6767
    assert payload["output"].get("greeting") == "hola, eva"
