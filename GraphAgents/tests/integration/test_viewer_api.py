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


def test_api_plan_returns_execution_order():
    # parse_qs entrega listas: {"agent": ["ads-analytics"]}
    status, payload = api_route("GET", "/api/plan", {"agent": ["ads-analytics"]}, None, ga_root=ROOT)
    assert status == 200
    assert payload["strategy"] == "sequential"
    assert [s["agent"] for s in payload["steps"]] == [
        "ctwa-insights", "sales-ledger", "ctwa-campaign-funnel",
        "blended-economics", "numbers-qa", "ctwa-report",
    ]
    assert payload["steps"][2]["inputs"]["insights_payload"] == "$state.meta_insights"


def test_api_plan_unknown_agent_is_404():
    status, payload = api_route("GET", "/api/plan", {"agent": ["nope"]}, None, ga_root=ROOT)
    assert status == 404


def test_api_plan_requires_agent_param():
    status, payload = api_route("GET", "/api/plan", {}, None, ga_root=ROOT)
    assert status == 400


def test_api_trace_requires_execution_id():
    status, payload = api_route("GET", "/api/trace", {}, None, ga_root=ROOT)
    assert status == 400
    assert "execution_id" in payload["error"]


def test_api_node_state_requires_params():
    status, payload = api_route("GET", "/api/node-state", {}, None, ga_root=ROOT)
    assert status == 400


def test_api_inspect_node_returns_files_and_checks():
    status, payload = api_route("GET", "/api/inspect", {"node": ["agent:ctwa-insights"]}, None, ga_root=ROOT)
    assert status == 200
    assert any(f["path"] == "graphs/ctwa_insights.py" for f in payload["files"])
    assert payload["checks"]["level"] == "C2"


def test_api_inspect_edge_returns_relationship_guarantees():
    status, payload = api_route(
        "GET", "/api/inspect",
        {"source": ["agent:ads-analytics"], "target": ["agent:ctwa-insights"], "kind": ["agent"]},
        None, ga_root=ROOT)
    assert status == 200
    assert any("G-WIRE" in r["rule"] for r in payload["checks"]["rules"])


def test_api_inspect_requires_node_or_edge():
    status, payload = api_route("GET", "/api/inspect", {}, None, ga_root=ROOT)
    assert status == 400


def test_api_checks_paints_system_green():
    status, payload = api_route("GET", "/api/checks", {}, None, ga_root=ROOT)
    assert status == 200
    assert all(v["ok"] for v in payload["nodes"].values())
    assert all(v["ok"] for v in payload["edges"].values())


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
