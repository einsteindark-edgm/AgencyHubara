"""Builder del SystemGraph desde manifests de plugins.

Lee los manifests con la misma convención que `hubara_agency/src/main.py`
(`_PLUGINS_MANIFEST_DIR = frontend_dashboard/src/plugins/`). NO importa
módulos Python — solo parsea YAML, así que es seguro de correr sin tener
todas las deps del plugin instaladas (deliberadamente: meta-tool no debe
caer si un plugin downstream rompe).

Diseño:
    - `build_system_graph(manifests_dir=None)` es la entry-point.
    - Si `manifests_dir` es None, usa la convención del repo.
    - Idempotent dado mismos manifests.
    - NO efectos colaterales — solo retorna `SystemGraph`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from src.plugins.system_map.domain.contracts import (
    Edge,
    Node,
    PluginSummary,
    Stats,
    SystemGraph,
)
from src.plugins.system_map.domain.orphan_detector import detect_orphans

SCHEMA_VERSION = "0.1.0"


def _default_manifests_dir() -> Path:
    """Misma convención que `hubara_agency/src/main.py`."""
    # hubara_agency/src/plugins/system_map/domain/builder.py
    # → up 5 levels = repo root
    repo_root = Path(__file__).resolve().parents[5]
    return repo_root / "frontend_dashboard" / "src" / "plugins"


def _load_manifests(manifests_dir: Path) -> tuple[list[tuple[str, dict[str, Any]]], list[str]]:
    """Devuelve [(plugin_id, manifest_dict)] + warnings.

    NO falla si un manifest está roto — emite warning y sigue. El system map
    es un meta-tool: tiene que funcionar incluso cuando otros plugins están
    en estado inconsistente (ese es justamente el caso de uso).
    """
    warnings: list[str] = []
    out: list[tuple[str, dict[str, Any]]] = []
    if not manifests_dir.exists():
        warnings.append(f"manifests_dir not found: {manifests_dir}")
        return out, warnings

    for plugin_dir in sorted(manifests_dir.iterdir()):
        if not plugin_dir.is_dir():
            continue
        if plugin_dir.name.startswith("_") or plugin_dir.name.startswith("."):
            continue
        manifest_path = plugin_dir / "plugin.yaml"
        if not manifest_path.exists():
            continue
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            warnings.append(f"{plugin_dir.name}: invalid YAML — {exc}")
            continue
        if not isinstance(manifest, dict):
            warnings.append(f"{plugin_dir.name}: manifest is not a dict")
            continue
        manifest_id = manifest.get("id")
        if manifest_id != plugin_dir.name:
            warnings.append(
                f"{plugin_dir.name}: manifest id ({manifest_id!r}) != directory name"
            )
            # Aún así lo incluimos — útil ver el mismatch en la UI.
        out.append((plugin_dir.name, manifest))

    return out, warnings


def _build_plugin_node(plugin_id: str, manifest: dict[str, Any]) -> Node:
    """Container node (grouping para React Flow `parentId`)."""
    return Node(
        id=f"plugin:{plugin_id}",
        kind="plugin",
        plugin_id=plugin_id,
        label=manifest.get("display_name") or plugin_id,
        data={
            "version": manifest.get("version", "?"),
            "description": manifest.get("description") or "",
            "depends_on": list(manifest.get("depends_on") or []),
            "has_frontend": "frontend" in manifest,
            "has_api": "api" in manifest,
            "has_agent": "agent" in manifest,
        },
    )


def _build_frontend_nodes(plugin_id: str, manifest: dict[str, Any]) -> list[Node]:
    """Sections + sidebar entries del manifest.frontend.contributes."""
    fe = manifest.get("frontend") or {}
    contributes = fe.get("contributes") or {}
    nodes: list[Node] = []

    for idx, section in enumerate(contributes.get("sections") or []):
        if not isinstance(section, dict):
            continue
        key = section.get("key") or f"unnamed-{idx}"
        nodes.append(
            Node(
                id=f"section:{plugin_id}:{key}",
                kind="section",
                plugin_id=plugin_id,
                label=section.get("label") or key,
                data={
                    "key": key,
                    "order": section.get("order"),
                    "icon": section.get("icon"),
                    "entry": fe.get("entry", "./frontend"),
                },
            )
        )

    for idx, sidebar in enumerate(contributes.get("sidebar") or []):
        if not isinstance(sidebar, dict):
            continue
        route = sidebar.get("route") or f"/unnamed-{idx}"
        nodes.append(
            Node(
                id=f"sidebar:{plugin_id}:{route}",
                kind="sidebar",
                plugin_id=plugin_id,
                label=sidebar.get("label") or route,
                data={
                    "route": route,
                    "icon": sidebar.get("icon"),
                },
            )
        )

    return nodes


def _build_api_nodes(plugin_id: str, manifest: dict[str, Any]) -> list[Node]:
    """API routers — `python_module` o cada `legacy_routers` entry."""
    api = manifest.get("api") or {}
    nodes: list[Node] = []

    legacy = api.get("legacy_routers") or []
    if legacy:
        # Cuando hay legacy_routers, GANA — el python_module se ignora (per main.py).
        for idx, lr in enumerate(legacy):
            if not isinstance(lr, dict):
                continue
            module = lr.get("module") or f"unnamed-{idx}"
            slug = module.rsplit(".", 1)[-1]
            nodes.append(
                Node(
                    id=f"api:{plugin_id}:{slug}",
                    kind="api_router",
                    plugin_id=plugin_id,
                    label=f"{lr.get('prefix', '/')} ({slug})",
                    data={
                        "module": module,
                        "prefix": lr.get("prefix"),
                        "tags": list(lr.get("tags") or []),
                        "source": "legacy_routers",
                    },
                )
            )
    elif "python_module" in api:
        module = api["python_module"]
        nodes.append(
            Node(
                id=f"api:{plugin_id}:main",
                kind="api_router",
                plugin_id=plugin_id,
                label=f"{api.get('prefix', '/')} (main)",
                data={
                    "module": module,
                    "prefix": api.get("prefix"),
                    "tags": list(api.get("tags") or []),
                    "source": "python_module",
                },
            )
        )

    return nodes


def _build_agent_nodes(plugin_id: str, manifest: dict[str, Any]) -> list[Node]:
    """Workers Temporal — cada uno con su task_queue."""
    agent = manifest.get("agent") or {}
    workers = agent.get("workers") or []
    nodes: list[Node] = []
    task_queues_seen: set[str] = set()

    for idx, worker in enumerate(workers):
        if not isinstance(worker, dict):
            continue
        name = worker.get("name") or f"worker-{idx}"
        task_queue = worker.get("task_queue") or ""
        nodes.append(
            Node(
                id=f"worker:{plugin_id}:{name}",
                kind="worker",
                plugin_id=plugin_id,
                label=name,
                data={
                    "name": name,
                    "module": worker.get("module"),
                    "task_queue": task_queue,
                    "replicas": (worker.get("deployment") or {}).get("replicas"),
                },
            )
        )
        if task_queue and task_queue not in task_queues_seen:
            task_queues_seen.add(task_queue)
            nodes.append(
                Node(
                    id=f"queue:{task_queue}",
                    kind="task_queue",
                    plugin_id=plugin_id,
                    label=task_queue,
                    data={"queue_name": task_queue},
                )
            )

    return nodes


def _build_edges(
    plugin_node: Node, contributed_nodes: list[Node], all_plugin_ids: set[str]
) -> list[Edge]:
    """Edges desde el plugin container hacia sus contribuciones + depends_on."""
    edges: list[Edge] = []

    # plugin → contributed nodes (sections, sidebar, api_routers, workers)
    for n in contributed_nodes:
        if n.kind == "task_queue":
            continue  # task_queue se conecta desde worker, no desde plugin
        kind: EdgeKindLiteral = "exposes" if n.kind == "api_router" else "contributes"  # type: ignore[assignment]
        edges.append(
            Edge(
                id=f"e:{plugin_node.id}->{n.id}",
                source=plugin_node.id,
                target=n.id,
                kind=kind,
            )
        )

    # worker → task_queue
    for n in contributed_nodes:
        if n.kind != "worker":
            continue
        tq = n.data.get("task_queue")
        if tq:
            edges.append(
                Edge(
                    id=f"e:{n.id}->queue:{tq}",
                    source=n.id,
                    target=f"queue:{tq}",
                    kind="consumes_queue",
                )
            )

    # plugin.depends_on → otro plugin
    for dep in plugin_node.data.get("depends_on", []):
        if dep in all_plugin_ids:
            edges.append(
                Edge(
                    id=f"e:{plugin_node.id}->plugin:{dep}",
                    source=plugin_node.id,
                    target=f"plugin:{dep}",
                    kind="depends_on",
                    label="depends_on",
                )
            )
        # Si dep no existe, el orphan_detector lo flag con depends_on_missing.

    return edges


# Workaround para el Literal type — silencia mypy sin importarlo en runtime.
EdgeKindLiteral = str  # noqa: E731 — type alias for inline use


def build_system_graph(manifests_dir: Path | None = None) -> SystemGraph:
    """Construye el SystemGraph completo. Idempotent.

    Args:
        manifests_dir: override del path de manifests. None = convención.

    Returns:
        SystemGraph completo con nodos, edges, plugins, stats, warnings.
    """
    base_dir = manifests_dir or _default_manifests_dir()
    manifests, warnings = _load_manifests(base_dir)

    all_nodes: list[Node] = []
    all_edges: list[Edge] = []
    plugin_summaries: list[PluginSummary] = []
    all_plugin_ids = {pid for pid, _ in manifests}

    for plugin_id, manifest in manifests:
        plugin_node = _build_plugin_node(plugin_id, manifest)
        contributed: list[Node] = []
        contributed.extend(_build_frontend_nodes(plugin_id, manifest))
        contributed.extend(_build_api_nodes(plugin_id, manifest))
        contributed.extend(_build_agent_nodes(plugin_id, manifest))

        # Edges del plugin
        all_edges.extend(_build_edges(plugin_node, contributed, all_plugin_ids))

        # Insertar al final para que el container quede listado antes de sus hijos
        # (consistente con expected reading order).
        all_nodes.append(plugin_node)
        all_nodes.extend(contributed)

        plugin_summaries.append(
            PluginSummary(
                id=plugin_id,
                display_name=plugin_node.label,
                version=plugin_node.data.get("version", "?"),
                description=plugin_node.data.get("description") or None,
                has_frontend=plugin_node.data.get("has_frontend", False),
                has_api=plugin_node.data.get("has_api", False),
                has_agent=plugin_node.data.get("has_agent", False),
                node_count=len([n for n in contributed if n.kind != "task_queue"]),
                orphan_count=0,  # se rellena después en detect_orphans
            )
        )

    # Orphan detection — mutates `is_orphan` + `orphan_reason` via replacement
    all_nodes = detect_orphans(all_nodes, all_edges, all_plugin_ids, warnings)

    # Recompute orphan_count por plugin (post-detection)
    orphans_per_plugin: dict[str, int] = {}
    for n in all_nodes:
        if n.is_orphan:
            orphans_per_plugin[n.plugin_id] = orphans_per_plugin.get(n.plugin_id, 0) + 1
    plugin_summaries = [
        PluginSummary(
            id=ps.id,
            display_name=ps.display_name,
            version=ps.version,
            description=ps.description,
            has_frontend=ps.has_frontend,
            has_api=ps.has_api,
            has_agent=ps.has_agent,
            node_count=ps.node_count,
            orphan_count=orphans_per_plugin.get(ps.id, 0),
        )
        for ps in plugin_summaries
    ]

    by_kind: dict[str, int] = {}
    for n in all_nodes:
        by_kind[n.kind] = by_kind.get(n.kind, 0) + 1

    stats = Stats(
        total_nodes=len(all_nodes),
        total_edges=len(all_edges),
        total_plugins=len(plugin_summaries),
        orphan_count=sum(1 for n in all_nodes if n.is_orphan),
        by_kind=by_kind,
    )

    return SystemGraph(
        version=SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        nodes=all_nodes,
        edges=all_edges,
        plugins=plugin_summaries,
        stats=stats,
        warnings=warnings,
    )
