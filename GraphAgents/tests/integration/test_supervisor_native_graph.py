"""G2 · el supervisor compila a un task graph NATIVO de langgraph (un grafo compuesto que
AgentSpan corre durable), no solo el threading in-process del LocalRuntime.

`build_agent(<supervisor>)` arma UN `StateGraph` donde cada agente es un nodo (resuelve su
binding `inputs:` desde el acumulador → corre su capability → mergea el output). Estado = un
canal `acc` LastValue (SIN reducer custom): cada nodo MERGEA EN CÓDIGO y devuelve el `acc`
completo, así last-write-wins es correcto para una cadena secuencial y es server-safe — un
reducer de merge custom se ignora server-side (L-14). Acumulador dinámico: los outputs
terminales (markdown, verdict) sobreviven, que una colección estática de canales dropearía.

El recovery por-AGENTE (con `checkpointer`) se EJERCITA en `test_durable_recovery.py`
(`test_supervisor_compuesto_recupera_por_agente`), no acá. Este test solo asierta la
composición. Skipea sin langgraph; corre el grafo compuesto SIN server — el smoke en el
server real de AgentSpan vive en test_agentspan_runtime.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

GA = Path(__file__).resolve().parents[2]
META_FIX = GA / "fixtures" / "meta_insights_campaigns.json"
ENT_FIX = GA / "fixtures" / "mcp_ad_entities.json"
SALES = {"sales": [
    {"date": "2026-06-15", "total_orders": 12, "total_revenue": 600000},
    {"date": "2026-06-16", "total_orders": 4, "total_revenue": 150000},
]}


def _seed() -> dict:
    return {
        "meta_insights": json.loads(META_FIX.read_text(encoding="utf-8")),
        "manual_sales": SALES,
        "entities_payload": json.loads(ENT_FIX.read_text(encoding="utf-8")),
    }


def test_supervisor_compila_a_grafo_nativo_y_produce_el_reporte() -> None:
    from sdk.loader import build_agent
    from sdk.manifest_model import load_manifest

    manifest = load_manifest(GA / "manifests" / "ads-analytics.taskgraph.yaml")
    graph = build_agent(manifest, GA)  # un CompiledStateGraph compuesto

    result = graph.invoke({"acc": _seed()})
    state = result["acc"]

    # el grafo compuesto threadeó las 6 etapas y dejó el reporte + el embudo:
    assert "markdown" in state and "Embudo por campaña" in state["markdown"]
    assert state["currency"] == "COP"
    assert [d["date"] for d in state["days"]] == ["2026-06-15", "2026-06-16"]
    # el complemento llegó por el task graph nativo: "Día del padre" recupera 120.
    padre = {c["campaign_id"]: c for c in state["campaigns"]}["120243118818600317"]
    assert padre["conversations"] == 120 and padre["conversation_source"] == "insights"


def test_parallel_compuesto_corre_los_independientes() -> None:
    # G2.x · un supervisor `strategy: parallel` compila a fan-out (START→a,b) + join. Los dos
    # extractores son INDEPENDIENTES (leen claves distintas del seed, escriben disjuntas) →
    # corren concurrentes y el join (operator.add sobre patches, server-safe L-14) los une.
    from sdk.loader import build_agent
    from sdk.manifest_model import load_manifest

    manifest = load_manifest(GA / "manifests" / "ads-extractors-parallel.taskgraph.yaml")
    graph = build_agent(manifest, GA)

    state = graph.invoke({"acc": _seed(), "patches": []})["acc"]

    # ambos extractores corrieron y sus outputs DISJUNTOS están en el acc:
    assert state["currency"] == "COP"  # ← ctwa-insights
    assert [d["date"] for d in state["insights"]] == ["2026-06-15", "2026-06-16"]  # ← ctwa-insights
    assert len(state["sales"]) == 2  # ← sales-ledger


def test_handoff_y_swarm_raisean_loud_pertenecen_al_LLM() -> None:
    # L-16: handoff/swarm son NATIVOS de AgentSpan y LLM-driven (handoffs por OnToolResult/
    # OnTextMention sobre el texto/tools del LLM) — NO se componen como StateGraph determinista.
    # build_supervisor_graph raisea loud para no shippear algo que cuelga/no aplica en el server.
    from sdk.loader import build_agent
    from sdk.manifest_model import TaskGraphManifest

    for strat in ("handoff", "swarm"):
        node = TaskGraphManifest.model_validate({
            "name": f"t-{strat}", "archetype": "supervisor", "strategy": strat,
            "agents": [{"uses": "agent://ctwa-insights@1", "inputs": {"payload": "$state.x"}}],
        })
        with pytest.raises(NotImplementedError, match="L-16"):
            build_agent(node, GA)


def test_router_compuesto_rutea_al_agente_elegido() -> None:
    # G2.x · un supervisor `strategy: router` compila a un grafo con conditional edges: el
    # `route` del input elige UN agente. ads-supervisor rutea a meta-insights (1ro, default)
    # o roas-cac (2do). Ruteamos a roas-cac (el NO-default): si el routing funciona corre
    # roas-cac; si cayera al default (meta-insights) reventaría por el port ausente → el éxito
    # PRUEBA la selección, no que "corra el primero".
    from sdk.loader import build_agent
    from sdk.manifest_model import load_manifest

    manifest = load_manifest(GA / "manifests" / "ads-supervisor.taskgraph.yaml")
    graph = build_agent(manifest, GA)

    seed = {"route": "roas-cac", "adsets": [{"id": "a", "roas": 2.0}, {"id": "b", "roas": 1.0}], "total_budget": 300.0}
    state = graph.invoke({"acc": seed})["acc"]

    assert state["analyzed_adsets"] == 2
    assert state["allocations"] == [{"id": "a", "budget": 200.0}, {"id": "b", "budget": 100.0}]
