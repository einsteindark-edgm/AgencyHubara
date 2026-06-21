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


def test_api_cases_lista_el_catalogo():
    status, payload = api_route("GET", "/api/cases", {}, None, ga_root=ROOT)
    assert status == 200
    ids = {c["id"] for c in payload["cases"]}
    assert {"diagnose-scale", "meta-insights-sample", "dia-del-padre-flujo"} <= ids
    d = next(c for c in payload["cases"] if c["id"] == "diagnose-scale")
    assert d["target"] == "tool:diagnose" and d["title"]  # liviano para el select


def test_api_cases_filtra_por_nodo():
    # un tool node → su caso exacto
    status, payload = api_route("GET", "/api/cases", {"node": ["tool:diagnose"]}, None, ga_root=ROOT)
    assert status == 200
    assert [c["id"] for c in payload["cases"]] == ["diagnose-scale"]
    # un supervisor (node agent:ads-analytics) ← el caso flow:ads-analytics (flow mapea a agent)
    status, payload = api_route("GET", "/api/cases", {"node": ["agent:ads-analytics"]}, None, ga_root=ROOT)
    assert [c["id"] for c in payload["cases"]] == ["dia-del-padre-flujo"]


def test_api_replay_corre_un_caso_y_compara_el_golden():
    status, payload = api_route("POST", "/api/replay", {}, {"case": "diagnose-scale"}, ga_root=ROOT)
    assert status == 200
    assert payload["matches"] is True
    assert payload["output"] == payload["golden"]
    assert payload["output"]["recommendation"] == "scale_budget"
    assert payload["target"] == "tool:diagnose"


def test_api_replay_expande_el_triple():
    # el panel "Probar" muestra el INPUT que entró: el seed resuelto + los ports inyectados.
    status, payload = api_route("POST", "/api/replay", {}, {"case": "meta-insights-sample"}, ga_root=ROOT)
    assert status == 200
    assert payload["inputs"]["seed"]["account_id"] == "act_1"
    assert payload["inputs"]["ports"]["meta_marketing_api"]  # el $ref se expandió a las filas
    assert payload["matches"] is True


def test_api_replay_caso_inexistente_es_404():
    status, payload = api_route("POST", "/api/replay", {}, {"case": "no-existe"}, ga_root=ROOT)
    assert status == 404


def test_api_replay_requiere_case():
    status, payload = api_route("POST", "/api/replay", {}, {}, ga_root=ROOT)
    assert status == 400


def test_api_replay_roto_es_422_sin_badge(monkeypatch):
    # la red de L-19: si el replay revienta (capability/loader roto), el endpoint da 422 con
    # un error y SIN 'matches' — así la UI nunca puede pintar ✓ sobre un replay que no corrió.
    def boom(case, ga_root):
        raise RuntimeError("capability rota")

    monkeypatch.setattr("sdk.replay.replay_case", boom)  # el import es local en replay_case_route
    status, payload = api_route("POST", "/api/replay", {}, {"case": "diagnose-scale"}, ga_root=ROOT)
    assert status == 422
    assert "error" in payload and "matches" not in payload


def test_durable_input_envuelve_el_seed_de_un_supervisor():
    # el bug: un supervisor en AgentSpan usa _SupervisorState{acc} → el seed va envuelto.
    # Pasarlo crudo (como hacía _run_on_agentspan) → el nodo hace dict(string) y revienta.
    from sdk.manifest_model import load_manifest
    from viewer.server import _durable_input

    m = load_manifest(ROOT / "manifests" / "ads-analytics.taskgraph.yaml")
    assert _durable_input(m, {"meta_insights": 1}) == {"acc": {"meta_insights": 1}}


def test_durable_input_parallel_agrega_patches():
    # parallel compila a _ParallelSupervisorState{acc, patches} (patches = reducer operator.add,
    # L-14) → el estado inicial necesita patches:[] o el fan-out no arranca.
    from sdk.manifest_model import load_manifest
    from viewer.server import _durable_input

    m = load_manifest(ROOT / "manifests" / "ads-extractors-parallel.taskgraph.yaml")
    assert m.strategy == "parallel"
    assert _durable_input(m, {"x": 1}) == {"acc": {"x": 1}, "patches": []}


def test_durable_input_crudo_para_una_capability():
    from sdk.manifest_model import load_manifest
    from viewer.server import _durable_input

    m = load_manifest(ROOT / "manifests" / "greeter.agent.yaml")  # no es supervisor
    assert _durable_input(m, {"name": "ada"}) == {"name": "ada"}


def test_durable_output_desenvuelve_el_acc_de_un_supervisor():
    from sdk.manifest_model import load_manifest
    from viewer.server import _durable_output

    m = load_manifest(ROOT / "manifests" / "ads-analytics.taskgraph.yaml")
    assert _durable_output(m, {"acc": {"verdict": "scale_budget"}}) == {"verdict": "scale_budget"}


def test_api_run_durable_requiere_case():
    status, payload = api_route("POST", "/api/run-durable", {}, {}, ga_root=ROOT)
    assert status == 400


def test_api_run_durable_rechaza_un_tool():
    # un tool no es un agente de AgentSpan → no corre en el runtime durable (usá 'probar').
    status, payload = api_route("POST", "/api/run-durable", {}, {"case": "diagnose-scale"}, ga_root=ROOT)
    assert status == 422
    assert "durable" in payload["error"].lower()


def test_api_run_durable_caso_inexistente_es_404():
    status, payload = api_route("POST", "/api/run-durable", {}, {"case": "no-existe"}, ga_root=ROOT)
    assert status == 404


def _server_up() -> bool:
    import urllib.request
    try:
        urllib.request.urlopen("http://localhost:6767/api/workflow/search?query=status+IN+(RUNNING)&size=1", timeout=3)
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _server_up(), reason="necesita el server AgentSpan en :6767")
def test_run_durable_es_async_devuelve_running_y_completa_en_background():
    # la MEJOR solución: /api/run-durable NO bloquea hasta completar — submitea con start(),
    # devuelve el execution-id con status 'running' al instante, y los workers completan en
    # background (el viewer pollea el trace y ve los nodos activarse en vivo).
    import time

    status, payload = api_route("POST", "/api/run-durable", {}, {"case": "dia-del-padre-flujo"}, ga_root=ROOT)
    assert status == 200, payload
    assert payload["execution_id"]
    assert payload["status"] == "running"  # NO 'completed' — no esperó (la versión sync devolvía completed)

    # y EVENTUALMENTE completa solo (los workers corren en background tras el return):
    from sdk.trace import fetch_workflow
    wf = {}
    for _ in range(40):
        wf = fetch_workflow(payload["execution_id"])
        if str(wf.get("status", "")).upper() in ("COMPLETED", "FAILED"):
            break
        time.sleep(0.5)
    assert str(wf.get("status", "")).upper() == "COMPLETED", wf.get("status")


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
