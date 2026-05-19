"""Detección de huérfanos en el SystemGraph.

Heurísticas V1 (todas estáticas, derivadas del manifest sin importar módulos):
  - empty_plugin: plugin que no contribuye frontend/api/agent.
  - frontend_incomplete: frontend_unit con section pero sin sidebar (UI
        inalcanzable desde nav), o sidebar sin section (botón sin destino).
        El detalle de cuál mitad falta vive en `data.has_sections` /
        `data.has_sidebars` — el frontend lo usa para colorear cada sub-parte.
  - worker_no_task_queue: worker sin task_queue (schema malformado).
  - api_router_no_prefix: api_router sin prefix.
  - depends_on_missing: plugin.depends_on apunta a plugin inexistente.

V2 candidates:
  - tool sin workflow caller (requiere parser AST de workflows.py)
  - feature frontend sin API call (requiere parsear código TS del entry)
"""
from __future__ import annotations

from src.plugins.system_map.domain.contracts import Edge, Node


def detect_orphans(
    nodes: list[Node],
    edges: list[Edge],
    all_plugin_ids: set[str],
    warnings: list[str],
) -> list[Node]:
    """Re-emite la lista con `is_orphan` + `orphan_reason` flagged."""
    plugin_nodes = [n for n in nodes if n.kind == "plugin"]

    # Detect: depends_on missing
    deps_missing: dict[str, str] = {}
    for p in plugin_nodes:
        for dep in p.data.get("depends_on", []):
            if dep not in all_plugin_ids:
                deps_missing[p.id] = dep
                warnings.append(f"{p.plugin_id}: depends_on '{dep}' not found")
                break

    new_nodes: list[Node] = []
    for n in nodes:
        is_orphan = False
        reason = None

        if n.kind == "plugin":
            d = n.data
            if not (d.get("has_frontend") or d.get("has_api") or d.get("has_agent")):
                is_orphan = True
                reason = "empty_plugin"
            elif n.id in deps_missing:
                is_orphan = True
                reason = "depends_on_missing"

        elif n.kind == "frontend_unit":
            # frontend_incomplete: XOR de las 2 mitades (una sí, otra no).
            # Si tiene AMBAS o NINGUNA, no flag (ninguna = no debería existir).
            has_sec = bool(n.data.get("has_sections"))
            has_sb = bool(n.data.get("has_sidebars"))
            if has_sec != has_sb:        # XOR
                is_orphan = True
                reason = "frontend_incomplete"

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
