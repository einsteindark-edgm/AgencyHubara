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

# --- sonda de recovery por-nodo a nivel CONDUCTOR (L-14) -----------------------------------
# AgentSpan corre cada nodo del grafo como una task de Conductor con retry. Esta sonda lo
# prueba a nivel SERVER (no el checkpointer in-process de LangGraph): un nodo del medio
# crashea la 1ra vez y tiene éxito al reintentar. Los workers son PROCESOS separados (fork) →
# la prueba de "no recomputó" es un LOG en archivo (cross-process), no un contador in-process.
_PROBE_LOG = os.path.join(os.environ.get("TMPDIR", "/tmp"), "ga_conductor_probe_log")
_PROBE_SENTINEL = os.path.join(os.environ.get("TMPDIR", "/tmp"), "ga_conductor_probe_sentinel")


def _build_conductor_probe_graph():
    """Grafo de PRUEBA (no producción) a→flaky_b→c. Cada nodo loguea su nombre a un archivo;
    flaky_b crashea la 1ra vez (sin sentinel) y pasa al reintentar. Si Conductor reintenta SOLO
    el nodo fallido, el LOG muestra A y C una vez y B dos."""
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    class S(TypedDict, total=False):
        x: int

    def a(s: S) -> dict:
        with open(_PROBE_LOG, "a") as f:
            f.write("A\n")
        return {"x": s.get("x", 0) + 1}

    def flaky_b(s: S) -> dict:
        with open(_PROBE_LOG, "a") as f:
            f.write("B\n")
        if not os.path.exists(_PROBE_SENTINEL):
            open(_PROBE_SENTINEL, "w").close()
            raise RuntimeError("crash B (1er intento) — Conductor debe reintentar SOLO B")
        return {"x": s["x"] + 10}

    def c(s: S) -> dict:
        with open(_PROBE_LOG, "a") as f:
            f.write("C\n")
        return {"x": s["x"] + 100}

    g = StateGraph(S)
    g.add_node("a", a)
    g.add_node("flaky_b", flaky_b)
    g.add_node("c", c)
    g.add_edge(START, "a")
    g.add_edge("a", "flaky_b")
    g.add_edge("flaky_b", "c")
    g.add_edge("c", END)
    return g.compile(name="conductor-recovery-probe")


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
    AgentRuntime). En el server, este grafo multi-nodo se descompone en tasks de Conductor
    por-nodo, con su execution-id (L-14). Lo que prueba el smoke: el COMPLEMENTO sobrevive el
    round-trip durable — "Día del padre" (entities=0) recupera 120 conversaciones de /insights."""
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


def test_supervisor_compuesto_corre_en_agentspan():
    """G2 · el supervisor ads-analytics (los 6 agentes compuestos en UN grafo por
    build_agent) corre durable en AgentSpan con su execution-id de Conductor. El reporte
    terminal (markdown + embudo) sale del run del task graph nativo, no del threading
    in-process del LocalRuntime."""
    import json

    from sdk.loader import build_agent
    from sdk.manifest_model import load_manifest
    from sdk.runtime import AgentSpanRuntime

    manifest = load_manifest(ROOT_PATH / "manifests" / "ads-analytics.taskgraph.yaml")
    graph = build_agent(manifest, ROOT_PATH)
    seed = {
        "meta_insights": json.loads((ROOT_PATH / "fixtures" / "meta_insights_campaigns.json").read_text(encoding="utf-8")),
        "manual_sales": {"sales": [{"date": "2026-06-15", "total_orders": 12, "total_revenue": 600000}]},
        "entities_payload": json.loads((ROOT_PATH / "fixtures" / "mcp_ad_entities.json").read_text(encoding="utf-8")),
    }

    ex = AgentSpanRuntime().run(graph, {"acc": seed})

    assert ex.status == "completed"
    assert ex.id  # execution-id de Conductor (visible en :6767)
    state = ex.output["acc"]
    assert "Embudo por campaña" in state["markdown"]
    padre = {c["campaign_id"]: c for c in state["campaigns"]}["120243118818600317"]
    assert padre["conversations"] == 120


def test_parallel_compuesto_corre_en_agentspan():
    """G2.x · un supervisor `parallel` (fan-out + join con reducer operator.add) corre en el
    SERVER real de AgentSpan — verifica el FORK_JOIN server-side (L-14: solo operator.add es
    server-safe). Los dos extractores independientes corren concurrentes y el join los une."""
    import json

    from sdk.loader import build_agent
    from sdk.manifest_model import load_manifest
    from sdk.runtime import AgentSpanRuntime

    manifest = load_manifest(ROOT_PATH / "manifests" / "ads-extractors-parallel.taskgraph.yaml")
    graph = build_agent(manifest, ROOT_PATH)
    seed = {
        "meta_insights": json.loads((ROOT_PATH / "fixtures" / "meta_insights_campaigns.json").read_text(encoding="utf-8")),
        "manual_sales": {"sales": [{"date": "2026-06-15", "total_orders": 12, "total_revenue": 600000}]},
    }

    ex = AgentSpanRuntime().run(graph, {"acc": seed, "patches": []})

    assert ex.status == "completed", f"el parallel no corrió en el server: {ex.error}"
    state = ex.output["acc"]
    assert state["currency"] == "COP"  # ← ctwa-insights (concurrente)
    assert len(state["sales"]) == 1  # ← sales-ledger (concurrente)


def test_router_compuesto_corre_en_agentspan():
    """G2.x · un supervisor `router` (conditional edges) corre en el SERVER real de AgentSpan
    — verifica que el conditional edge compila a Conductor (L-14: local ≠ server). Rutea a
    roas-cac (el NO-default) → si el routing server-side funciona, llegan las allocations."""
    from sdk.loader import build_agent
    from sdk.manifest_model import load_manifest
    from sdk.runtime import AgentSpanRuntime

    manifest = load_manifest(ROOT_PATH / "manifests" / "ads-supervisor.taskgraph.yaml")
    graph = build_agent(manifest, ROOT_PATH)
    seed = {"route": "roas-cac", "adsets": [{"id": "a", "roas": 2.0}, {"id": "b", "roas": 1.0}], "total_budget": 300.0}

    ex = AgentSpanRuntime().run(graph, {"acc": seed})

    assert ex.status == "completed", f"el router no corrió en el server: {ex.error}"
    assert ex.output["acc"]["allocations"] == [{"id": "a", "budget": 200.0}, {"id": "b", "budget": 100.0}]


def test_conductor_reintenta_el_nodo_fallido_sin_recomputar_los_previos():
    """G2.x · recovery por-nodo a nivel CONDUCTOR (cierra el guard que L-14 dejó abierto).
    Un grafo multi-nodo corre como tasks de Conductor por-nodo; cuando flaky_b crashea, el
    server REINTENTA esa task y completa. La prueba de que NO recomputó los previos: el LOG
    (cross-process, los workers son procesos separados) muestra A y C UNA vez, B dos."""
    from sdk.runtime import AgentSpanRuntime

    for p in (_PROBE_LOG, _PROBE_SENTINEL):
        if os.path.exists(p):
            os.remove(p)

    ex = AgentSpanRuntime().run(_build_conductor_probe_graph(), {"x": 0})

    assert ex.status == "completed", f"el server no recuperó del crash: {ex.error}"
    assert ex.output.get("x") == 111  # 1 + 10 + 100 (idempotente; el LOG es la prueba real)

    with open(_PROBE_LOG) as f:
        log = f.read().split()
    assert log.count("B") >= 2, f"flaky_b no reintentó en Conductor: {log}"
    assert log.count("A") == 1, f"el nodo A se RECOMPUTÓ en el retry de B (no hubo recovery por-nodo): {log}"
    assert log.count("C") == 1, f"el nodo C corrió de más: {log}"


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
