"""Detección de huérfanos en el SystemGraph.

Heurísticas V1 (todas estáticas, derivadas del manifest sin importar módulos):
  - empty_plugin: plugin que no contribuye frontend/api/agent.
  - section_without_sidebar: plugin tiene >=1 section pero 0 sidebar entries.
        Semántica: la section es inalcanzable desde la nav del shell.
  - sidebar_without_section: plugin tiene >=1 sidebar pero 0 sections.
        Semántica: el botón del sidebar no tiene destino renderizable.
  - worker_no_task_queue: worker sin task_queue (schema malformado).
  - api_router_no_prefix: api_router sin prefix.
  - depends_on_missing: plugin.depends_on apunta a plugin que no existe.

Nota sobre section ↔ sidebar matching:
    El schema NO impone relación entre `section.key` y `sidebar.route` — son
    campos independientes que el shell del frontend (App.tsx) conecta vía
    código (no manifest). Por eso la heurística V1 que comparaba keys con
    routes producía falsos positivos (chats: section `chat` vs sidebar
    `/chats` — singular vs plural, ambos OK en runtime).

    V2: parsear App.tsx o plugins-sync.ts para detectar el mapping real
    section.key → onClick handler en el sidebar.

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
    plugin_nodes = [n for n in nodes if n.kind == "plugin"]

    # Counts por plugin: ¿cuántas sections / sidebar tiene cada uno?
    # Heurística V2-corregida: si tiene 0 sidebars pero >=1 sections → las
    # sections son orphan (UI nav imposible). Y viceversa.
    section_count_by_plugin: dict[str, int] = {}
    sidebar_count_by_plugin: dict[str, int] = {}
    for n in nodes:
        if n.kind == "section":
            section_count_by_plugin[n.plugin_id] = (
                section_count_by_plugin.get(n.plugin_id, 0) + 1
            )
        elif n.kind == "sidebar":
            sidebar_count_by_plugin[n.plugin_id] = (
                sidebar_count_by_plugin.get(n.plugin_id, 0) + 1
            )

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
            # Section orphan SII el plugin no declara NINGÚN sidebar entry.
            # Si tiene >=1 sidebar, asumimos que el shell conecta los pares
            # vía código (no manifest) — no es nuestro trabajo verificarlo.
            sidebar_count = sidebar_count_by_plugin.get(n.plugin_id, 0)
            if sidebar_count == 0:
                is_orphan = True
                reason = "section_without_sidebar"

        elif n.kind == "sidebar":
            # Idem: orphan SII el plugin no declara NINGUNA section.
            section_count = section_count_by_plugin.get(n.plugin_id, 0)
            if section_count == 0:
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
