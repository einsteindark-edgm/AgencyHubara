"""Todo agente de PRODUCCIÓN es visible en el grupo Workflows de Acktos Studio.

El grupo "Workflows" del catálogo (vscode-hubara/src/trees/catalogTree.ts →
`workflows()`) lista las raíces operables del sistema:

  (a) supervisores con sub-agentes (aristas `agent` salientes) que nadie más
      supervisa — los taskgraphs, y
  (b) agentes standalone ENTRY-POINT — `exposes_as_tool: false` y sin
      supervisor (los dispatcha un sistema externo, p.ej. hubara vía SSM).

`_workflow_roots` acá es el ESPEJO documentado de ese filtro TS (mismo input:
el payload de `build_graph`). El invariante que este guard protege: un agente
de producción (sin `group:`) o está supervisado (visible dentro de la clausura
del workflow de su supervisor) o es raíz — nunca invisible. Caso que lo parió:
window-strategist (analyzer standalone, L-27) no aparecía como workflow.
"""
from __future__ import annotations

from pathlib import Path

from sdk.graph import build_graph

GA_ROOT = Path(__file__).resolve().parents[2]


def _workflow_roots(graph: dict) -> set[str]:
    """Espejo del filtro de catalogTree.workflows() — si cambiás uno, cambiá el otro."""
    agent_edges = [e for e in graph["edges"] if e["kind"] == "agent"]
    supervised = {e["target"] for e in agent_edges}
    return {
        n["id"]
        for n in graph["nodes"]
        if n["kind"] == "agent"
        and n["id"] not in supervised
        and (
            any(e["source"] == n["id"] for e in agent_edges)
            or n.get("exposes_as_tool") is False
        )
    }


def test_window_strategist_es_raiz_de_workflow():
    roots = _workflow_roots(build_graph(GA_ROOT))
    assert "agent:window-strategist" in roots, roots


def test_ningun_agente_de_produccion_queda_invisible():
    graph = build_graph(GA_ROOT)
    roots = _workflow_roots(graph)
    supervised = {e["target"] for e in graph["edges"] if e["kind"] == "agent"}
    invisibles = [
        n["id"]
        for n in graph["nodes"]
        if n["kind"] == "agent"
        and n.get("group") is None  # producción (demo/variant quedan fuera)
        and n["id"] not in supervised
        and n["id"] not in roots
    ]
    assert invisibles == [], (
        f"agentes de producción sin workflow en Acktos Studio: {invisibles} — "
        "o los compone un supervisor (uses: agent://...) o son entry-points "
        "(exposes_as_tool: false)"
    )


def test_el_espejo_detecta_un_entry_point_mal_declarado():
    """Caso negativo sintético: un standalone con exposes_as_tool: true NO es
    raíz — es exactamente la omisión que dejaría un pod invisible."""
    graph = {
        "nodes": [{"id": "agent:solo", "kind": "agent", "exposes_as_tool": True, "group": None}],
        "edges": [],
    }
    assert _workflow_roots(graph) == set()
    graph["nodes"][0]["exposes_as_tool"] = False
    assert _workflow_roots(graph) == {"agent:solo"}
