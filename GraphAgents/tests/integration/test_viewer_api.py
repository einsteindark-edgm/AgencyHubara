"""V2 del explorer: el backend vivo (stdlib `http.server`, cero deps nuevas).

Se testea el CORE ruteable (`api_route` / `run_agent`) SIN abrir un socket —
determinista y rápido. El `BaseHTTPRequestHandler` es glue fino sobre esto.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from viewer.server import api_route, run_agent

ROOT = Path(__file__).resolve().parents[2]  # .../GraphAgents


def test_api_graph_returns_system_graph():
    status, payload = api_route("GET", "/api/graph", {}, None, ga_root=ROOT)
    assert status == 200
    ids = {n["id"] for n in payload["nodes"]}
    assert "agent:ads-supervisor" in ids
    assert payload["edges"]  # hay aristas


def test_api_health_ok():
    status, payload = api_route("GET", "/api/health", {}, None, ga_root=ROOT)
    assert status == 200
    assert payload["ok"] is True


def test_run_tool_only_agent_greeter():
    res = run_agent(ROOT, "greeter", {"name": "mundo"})
    assert res["status"] == "completed"
    assert res["output"] == {"greeting": "hola, mundo"}


def test_run_endpoint_greeter():
    status, payload = api_route(
        "POST", "/api/run", {}, {"agent": "greeter", "input": {"name": "ada"}}, ga_root=ROOT
    )
    assert status == 200
    assert payload["output"] == {"greeting": "hola, ada"}
    assert payload["status"] == "completed"


def test_run_rejects_port_consuming_agent():
    status, payload = api_route(
        "POST", "/api/run", {}, {"agent": "meta-insights", "input": {}}, ga_root=ROOT
    )
    assert status == 422
    assert "meta_marketing_api" in payload["error"]


def test_run_requires_agent_field():
    status, payload = api_route("POST", "/api/run", {}, {}, ga_root=ROOT)
    assert status == 400


def test_unknown_route_is_404():
    status, payload = api_route("GET", "/api/nope", {}, None, ga_root=ROOT)
    assert status == 404


def test_run_invalid_runtime_is_400():
    status, payload = api_route(
        "POST", "/api/run", {}, {"agent": "greeter", "input": {}, "runtime": "nope"}, ga_root=ROOT
    )
    assert status == 400


def _has(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None


@pytest.mark.skipif(_has("langgraph"), reason="con langgraph el path agentspan corre de verdad (ver test_agentspan_runtime)")
def test_run_agentspan_degrades_gracefully_without_deps():
    """Sin langgraph/agentspan (el loop local), pedir runtime=agentspan NO crashea:
    devuelve un error claro (no una excepción) — la UI nunca rompe."""
    status, payload = api_route(
        "POST", "/api/run", {},
        {"agent": "greeter", "input": {"name": "x"}, "runtime": "agentspan"}, ga_root=ROOT,
    )
    assert status == 422
    assert payload["status"] == "failed"
    assert payload.get("runtime") == "agentspan"
    assert payload["error"]
