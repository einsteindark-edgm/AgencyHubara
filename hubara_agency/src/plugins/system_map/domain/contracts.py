"""DTOs frozen para el system graph (R-JSON safe).

Diseño: el grafo es producto-de-builder, NO state. Cada `build_system_graph()`
es idempotent dado mismos manifests. Por eso todos los dataclasses son frozen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

NodeKind = Literal[
    "plugin",
    "section",      # frontend.contributes.sections entry
    "sidebar",      # frontend.contributes.sidebar entry
    "api_router",   # api.python_module o cada api.legacy_routers entry
    "api_endpoint", # HTTP route concreto (si extraído por introspection — V2)
    "worker",       # agent.workers entry
    "task_queue",   # task_queue de un worker (puede ser shared)
]

EdgeKind = Literal[
    "depends_on",       # plugin → plugin (via manifest.depends_on)
    "contributes",      # plugin → section/sidebar/api_router/worker
    "mounts_on",        # api_router → api_endpoint (parent → child) — V2
    "consumes_queue",   # worker → task_queue
    "exposes",          # plugin → api_router (alias semántico de contributes)
    "opens",            # sidebar → section (mismo plugin — el shell conecta vía código)
    "uses_api",         # section → api_router (mismo plugin, lazy match)
    "invokes_worker",   # api_router → worker (DETECTADO de get_task_queue() en código Python)
]


OrphanReason = Literal[
    "empty_plugin",                # plugin sin frontend/api/agent
    "section_without_sidebar",     # frontend section sin sidebar entry equivalente
    "sidebar_without_section",     # sidebar entry sin section declarada
    "worker_no_task_queue",        # worker sin task_queue declarado (schema bug)
    "api_router_no_prefix",        # api_router sin prefix (legacy_router malformado)
    "manifest_invalid",            # YAML parse error o id mismatch (informativo)
    "depends_on_missing",          # plugin.depends_on apunta a un plugin que no existe
]


# Status del plugin según qué layers contribuye. NO es orphan (frontend_only
# es intencional para plugins como eta/orders), es indicador visual.
CompletenessStatus = Literal[
    "complete",         # frontend + api + agent
    "frontend_only",    # solo frontend (deliberado: eta, orders, agents_admin)
    "api_only",         # solo api (e.g. system_map)
    "agent_only",       # solo agent (worker headless)
    "frontend_api",     # frontend + api (sin agent)
    "frontend_agent",   # frontend + agent (sin api directa)
    "api_agent",        # api + agent (backend service)
    "empty",            # ninguno (depends_on hub or pure dependency)
]


@dataclass(frozen=True)
class Node:
    """Un nodo del grafo. Renderizable directamente como React Flow node."""

    id: str                          # unique key — eg "plugin:chats", "api:chats:sales"
    kind: NodeKind
    plugin_id: str                   # plugin owner (incluso para tipo 'plugin' donde id==plugin_id)
    label: str                       # display label
    data: dict                       # extra metadata serializable (paths, prefixes, etc.)
    is_orphan: bool = False
    orphan_reason: OrphanReason | None = None


@dataclass(frozen=True)
class Edge:
    """Una arista direccional source → target."""

    id: str
    source: str                      # Node.id
    target: str                      # Node.id
    kind: EdgeKind
    label: str | None = None         # display label opcional


@dataclass(frozen=True)
class PluginSummary:
    """Resumen 1-line por plugin para el sidebar del UI."""

    id: str
    display_name: str
    version: str
    description: str | None
    has_frontend: bool
    has_api: bool
    has_agent: bool
    completeness: CompletenessStatus
    node_count: int                  # nodos contribuidos (excluye el container plugin)
    orphan_count: int                # nodos suyos flagged como orphan


@dataclass(frozen=True)
class Stats:
    """Totales agregados — útil para el header de la UI."""

    total_nodes: int
    total_edges: int
    total_plugins: int
    orphan_count: int
    by_kind: dict                    # {NodeKind: int}


@dataclass(frozen=True)
class SystemGraph:
    """Snapshot completo del sistema para render."""

    version: str                     # version del schema (este DTO)
    generated_at: str                # ISO 8601 UTC
    nodes: list[Node]
    edges: list[Edge]
    plugins: list[PluginSummary]
    stats: Stats
    warnings: list[str] = field(default_factory=list)  # info no-fatal del builder
