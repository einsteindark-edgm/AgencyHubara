"""Detección de huérfanos en el SystemGraph.

Heurísticas V1 (todas estáticas, derivadas del manifest sin importar módulos):
  - empty_plugin: plugin que no contribuye frontend/api/agent.
  - section_without_sidebar: section declarada sin sidebar entry equivalente.
  - sidebar_without_section: sidebar entry sin section.
  - worker_no_task_queue: worker sin task_queue (schema malformado).
  - api_router_no_prefix: api_router sin prefix.
  - depends_on_missing: plugin.depends_on apunta a plugin que no existe.

V2 candidates (NO V1, requieren import introspection):
  - tool sin workflow caller
  - feature frontend sin API call
  - API sin frontend consumer
"""
from __future__ import annotations

from src.plugins.system_map.domain.contracts import Edge, Node


def detect_orphans(
    nodes: list[Node],
    edges: list[Edge],
    all_plugin_ids: set[str],
    warnings: list[str],
) -> list[Node]:
    """Re-emite la lista de nodos con `is_orphan` + `orphan_reason` flagged.

    Las heurísticas son aditivas — el primer flag gana. Los nodos son frozen,
    por eso se construye una lista nueva con `Node(...)` replacement.
    """
    # Index por kind para detección eficiente
    by_id: dict[str, Node] = {n.id: n for n in nodes}
    plugin_nodes = [n for n in nodes if n.kind == "plugin"]

    # Sets para detección de pares section/sidebar
    sections_by_plugin: dict[str, set[str]] = {}
    sidebars_by_plugin: dict[str, set[str]] = {}
    for n in nodes:
        if n.kind == "section":
            key = n.data.get("key", "")
            sections_by_plugin.setdefault(n.plugin_id, set()).add(key)
        elif n.kind == "sidebar":
            # heurística: matchear sidebar.route contra section.key es laxo,
            # comparar el segmento final de la ruta con la key.
            route = n.data.get("route") or ""
            tail = route.lstrip("/").split("/", 1)[0]
            sidebars_by_plugin.setdefault(n.plugin_id, set()).add(tail)

    # Detect: depends_on missing
    deps_missing: dict[str, str] = {}  # plugin.id → missing dep id
    for p in plugin_nodes:
        for dep in p.data.get("depends_on", []):
            if dep not in all_plugin_ids:
                deps_missing[p.id] = dep
                warnings.append(f"{p.plugin_id}: depends_on '{dep}' not found")
                break  # only flag first missing

    new_nodes: list[Node] = []
    for n in nodes:
        is_orphan = False
        reason = None

        if n.kind == "plugin":
            # empty_plugin: ningún frontend/api/agent
            d = n.data
            if not (d.get("has_frontend") or d.get("has_api") or d.get("has_agent")):
                is_orphan = True
                reason = "empty_plugin"
            elif n.id in deps_missing:
                is_orphan = True
                reason = "depends_on_missing"

        elif n.kind == "section":
            key = n.data.get("key", "")
            sb = sidebars_by_plugin.get(n.plugin_id, set())
            # heurística laxa: section sin sidebar entry que mencione la key
            if key not in sb and key.replace("_", "-") not in sb:
                is_orphan = True
                reason = "section_without_sidebar"

        elif n.kind == "sidebar":
            route = n.data.get("route") or ""
            tail = route.lstrip("/").split("/", 1)[0]
            sc = sections_by_plugin.get(n.plugin_id, set())
            if tail not in sc and tail.replace("-", "_") not in sc:
                is_orphan = True
                reason = "sidebar_without_section"

        elif n.kind == "worker":
            if not n.data.get("task_queue"):
                is_orphan = True
                reason = "worker_no_task_queue"

        elif n.kind == "api_router":
            if not n.data.get("prefix"):
                is_orphan = True
                reason = "api_router_no_prefix"

        new_nodes.append(
            Node(
                id=n.id,
                kind=n.kind,
                plugin_id=n.plugin_id,
                label=n.label,
                data=n.data,
                is_orphan=is_orphan,
                orphan_reason=reason,
            )
        )

    return new_nodes
