"""Inspección del grafo para el explorer: qué FILES respaldan cada nodo/arista, y qué
CHECKS los garantizan. Golden sobre el catálogo real (como test_system_graph): los paths
y los veredictos son comportamiento observable. Incluye el caso NEGATIVO — una relación
rota debe dar el check en rojo (es lo que hace confiable el overlay de certificación).
"""
from __future__ import annotations

from pathlib import Path

from sdk.graph import build_graph
from sdk.inspect import edge_checks, edge_files, inspect_node, node_checks, node_files, system_checks

ROOT = Path(__file__).resolve().parents[2]  # .../GraphAgents
G = build_graph(ROOT)


def _node(node_id: str) -> dict:
    return next(n for n in G["nodes"] if n["id"] == node_id)


def _edge(source: str, target: str) -> dict:
    return next(e for e in G["edges"] if e["source"] == source and e["target"] == target)


def _paths(files: list[dict]) -> set[str]:
    return {f["path"] for f in files}


# --- files de un nodo ------------------------------------------------------------------

def test_tool_node_files_are_contract_impl_adapters_test():
    files = node_files(_node("tool:meta-ads-insights"), ROOT)
    assert _paths(files) == {
        "tools/meta_ads_insights/tool.yaml",
        "tools/meta_ads_insights/impl.py",
        "tools/meta_ads_insights/adapters/agentspan.py",
        "tools/meta_ads_insights/adapters/langgraph.py",
        "tests/tools/test_meta_ads_insights.py",
    }
    # cada file trae su abspath para el link vscode://file/
    assert all(f["abspath"].endswith(f["path"]) for f in files)


def test_agent_node_files_include_manifest_capability_and_tests():
    paths = _paths(node_files(_node("agent:ctwa-insights"), ROOT))
    assert "manifests/ctwa-insights.agent.yaml" in paths
    assert "graphs/ctwa_insights.py" in paths              # la capability (StateGraph)
    assert "tests/graphs/test_ctwa_insights_golden.py" in paths


def test_supervisor_node_files_point_to_taskgraph():
    paths = _paths(node_files(_node("agent:ads-analytics"), ROOT))
    assert "manifests/ads-analytics.taskgraph.yaml" in paths


def test_port_node_files_point_to_connectorkit():
    paths = _paths(node_files(_node("port:meta_marketing_api"), ROOT))
    assert paths == {"sdk/connectorkit/ports.py"}


# --- files de una RELACIÓN (la línea entre nodos) --------------------------------------

def test_uses_edge_files_are_agent_manifest_and_tool_contract():
    paths = _paths(edge_files(_edge("agent:ctwa-insights", "tool:meta-ads-insights"), ROOT))
    assert "manifests/ctwa-insights.agent.yaml" in paths      # dónde se escribe el binding
    assert "tools/meta_ads_insights/tool.yaml" in paths       # el contrato que el binding respeta


def test_agent_edge_files_are_supervisor_taskgraph_and_referenced_agent():
    paths = _paths(edge_files(_edge("agent:ads-analytics", "agent:ctwa-insights"), ROOT))
    assert "manifests/ads-analytics.taskgraph.yaml" in paths  # la composición
    assert "manifests/ctwa-insights.agent.yaml" in paths      # el agente referenciado


# --- checks que GARANTIZAN un nodo -----------------------------------------------------

def test_supervisor_node_checks_are_green_with_wire_and_bind_agent():
    c = node_checks(_node("agent:ads-analytics"), ROOT)
    assert c["level"] == "C2"
    rules = {r["rule"]: r["ok"] for r in c["rules"]}
    assert rules["G-WIRE"] is True and rules["G-BIND-AGENT"] is True and rules["G-RUN-SIG"] is True


def test_tool_node_checks_carry_tool_rules():
    c = node_checks(_node("tool:meta-ads-insights"), ROOT)
    rules = {r["rule"] for r in c["rules"]}
    assert {"T-IMPL", "T-DUR", "G-AGNOSTIC"} <= rules
    assert c["level"] in {"C2", "C3"}


# --- checks que GARANTIZAN una RELACIÓN ------------------------------------------------

def test_uses_edge_is_guaranteed_by_g_bind():
    c = edge_checks(_edge("agent:roas-cac", "tool:recommend-budget"), ROOT)
    assert c["rules"] and all(r["ok"] for r in c["rules"])
    assert any(r["rule"].startswith("G-BIND") for r in c["rules"])


def test_composition_edge_is_guaranteed_by_wire_and_bind_agent():
    c = edge_checks(_edge("agent:ads-analytics", "agent:ctwa-insights"), ROOT)
    rules = {r["rule"]: r["ok"] for r in c["rules"]}
    assert rules.get("G-BIND-AGENT · agente resuelve") is True
    assert rules.get("G-WIRE · inputs declarados") is True


def test_router_edge_has_no_wire_rule():
    # ads-supervisor es router → sus branches NO requieren inputs (G-WIRE los exime)
    c = edge_checks(_edge("agent:ads-supervisor", "agent:meta-insights"), ROOT)
    rules = {r["rule"] for r in c["rules"]}
    assert any("G-BIND-AGENT" in r for r in rules)
    assert not any("G-WIRE" in r for r in rules)


def test_broken_relationship_shows_red_check():
    """El caso que hace CONFIABLE el overlay: una arista `uses` a una tool inexistente
    da G-BIND en rojo. Si el explorer la pintara verde, no se podría confiar a solo visual."""
    broken = {"source": "agent:ctwa-insights", "target": "tool:no-existe", "kind": "uses"}
    c = edge_checks(broken, ROOT)
    assert any(not r["ok"] for r in c["rules"])


def test_consumes_edge_is_guaranteed_by_real_port_resolution():
    """La relación `consumes` debe correr un check REAL (el port resuelve en el ConnectorKit),
    no una etiqueta verde hardcodeada — eso sería falsa confianza."""
    c = edge_checks(_edge("agent:meta-insights", "port:meta_marketing_api"), ROOT)
    assert c["rules"] and all(r["ok"] for r in c["rules"])
    assert not any("G-PORT" in r["rule"] for r in c["rules"])  # G-PORT es otra cosa (AST), no esto


def test_consumes_edge_to_unknown_port_is_red():
    """MUST anti-falsa-confianza: un `consumes` a un port AUSENTE del registry da ROJO."""
    broken = {"source": "agent:meta-insights", "target": "port:no-existe", "kind": "consumes"}
    c = edge_checks(broken, ROOT)
    assert c["rules"] and any(not r["ok"] for r in c["rules"])


def test_port_node_checks_resolve_in_registry():
    c = node_checks(_node("port:meta_marketing_api"), ROOT)
    assert c["rules"] and all(r["ok"] for r in c["rules"])


def test_supervisor_node_files_include_integration_tests():
    """Falso-negativo de files: un supervisor (cap=None) tiene sus tests en tests/integration/;
    deben listarse para que el operador los encuentre en el IDE."""
    paths = _paths(node_files(_node("agent:ads-analytics"), ROOT))
    assert any(p.startswith("tests/integration/") and "ads_analytics" in p for p in paths)


# --- el overlay del sistema (correr las certs en el viewer) ----------------------------

def test_system_checks_paints_every_node_and_edge_green():
    res = system_checks(G, ROOT)
    assert all(v["ok"] for v in res["nodes"].values()), [k for k, v in res["nodes"].items() if not v["ok"]]
    assert all(v["ok"] for v in res["edges"].values()), [k for k, v in res["edges"].items() if not v["ok"]]


def test_inspect_node_bundles_files_and_checks():
    out = inspect_node(_node("agent:ctwa-report"), ROOT)
    assert "files" in out and "checks" in out and out["files"]
