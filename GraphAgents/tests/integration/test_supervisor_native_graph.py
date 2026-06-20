"""G2 · el supervisor compila a un task graph NATIVO de langgraph (un grafo compuesto que
AgentSpan corre durable), no solo el threading in-process del LocalRuntime.

`build_agent(<supervisor>)` arma UN `StateGraph` donde cada agente es un nodo (resuelve su
binding `inputs:` desde el acumulador → corre su capability → mergea el output). Estado =
un canal `acc` con reducer de merge (acumulador dinámico; los outputs terminales —markdown,
verdict— sobreviven, que una colección estática de canales dropearía). Con `checkpointer`
hereda el recovery por-nodo de Phase C (por-AGENTE acá).

Skipea sin langgraph. Corre el grafo compuesto SIN server (solo langgraph) — el smoke en el
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
