"""V0 del explorer: el serializador del SISTEMA a grafo (nodes+edges) desde el
registry. **Una sola fuente** para todos los frontends — el CLI (`graph
--format=mermaid|json`), el visor React Flow y el backend http leen ESTE grafo.

Se asierta el COMPORTAMIENTO observable sobre el catálogo real (no la
implementación): qué nodos hay, de qué tipo, con qué contrato/cert, y cómo están
cableados (uses / agent:// / consumes). Es el golden estructural del sistema.
"""
from __future__ import annotations

from pathlib import Path

from sdk.graph import build_graph, to_mermaid

ROOT = Path(__file__).resolve().parents[2]  # .../GraphAgents


def _ids(graph: dict, kind: str) -> set[str]:
    return {n["id"] for n in graph["nodes"] if n["kind"] == kind}


def _node(graph: dict, node_id: str) -> dict:
    return next(n for n in graph["nodes"] if n["id"] == node_id)


def _edge(graph: dict, source: str, target: str) -> dict | None:
    return next(
        (e for e in graph["edges"] if e["source"] == source and e["target"] == target),
        None,
    )


def test_nodes_cover_tools_agents_ports():
    g = build_graph(ROOT)
    assert {"tool:hello", "tool:recommend-budget"} <= _ids(g, "tool")
    assert {
        "agent:meta-insights",
        "agent:roas-cac",
        "agent:greeter",
        "agent:ads-supervisor",
    } <= _ids(g, "agent")
    assert "port:meta_marketing_api" in _ids(g, "port")


def test_tool_node_carries_contract_and_cert():
    g = build_graph(ROOT)
    hello = _node(g, "tool:hello")
    assert hello["label"] == "hello"
    assert hello["side_effect"] == "pure"
    assert hello["approval_required"] is False
    assert hello["certification"] in {"none", "C0", "C1", "C2", "C3"}


def test_supervisor_node_has_archetype_and_strategy():
    g = build_graph(ROOT)
    sup = _node(g, "agent:ads-supervisor")
    assert sup["archetype"] == "supervisor"
    assert sup["strategy"] == "router"


def test_agent_node_carries_publish_consumes_exposes():
    g = build_graph(ROOT)
    mi = _node(g, "agent:meta-insights")
    assert mi["archetype"] == "extractor"
    assert mi["publish"] == "mcp"
    assert mi["exposes_as_tool"] is True
    assert "meta_marketing_api" in mi["consumes"]


def test_edges_supervisor_routes_to_agents():
    g = build_graph(ROOT)
    e = _edge(g, "agent:ads-supervisor", "agent:meta-insights")
    assert e is not None and e["kind"] == "agent"
    assert e["strategy"] == "router"
    assert _edge(g, "agent:ads-supervisor", "agent:roas-cac") is not None


def test_edges_agent_uses_tool():
    g = build_graph(ROOT)
    e = _edge(g, "agent:roas-cac", "tool:recommend-budget")
    assert e is not None and e["kind"] == "uses"
    assert _edge(g, "agent:greeter", "tool:hello")["kind"] == "uses"


def test_edges_agent_consumes_port():
    g = build_graph(ROOT)
    e = _edge(g, "agent:meta-insights", "port:meta_marketing_api")
    assert e is not None and e["kind"] == "consumes"
    assert _edge(g, "agent:roas-cac", "port:meta_marketing_api") is not None


def test_mermaid_render_smoke():
    g = build_graph(ROOT)
    m = to_mermaid(g)
    assert m.startswith("flowchart")
    assert "ads-supervisor" in m
    assert "recommend-budget" in m
    assert "-->" in m  # al menos una arista estructural
