"""Builder del SystemGraph desde manifests de plugins + scan de código.

Lee los manifests con la misma convención que `hubara_agency/src/main.py`
(`_PLUGINS_MANIFEST_DIR = frontend_dashboard/src/plugins/`). NO importa
módulos Python — solo parsea YAML, así que es seguro de correr sin tener
todas las deps del plugin instaladas (deliberadamente: meta-tool no debe
caer si un plugin downstream rompe).

Adicionalmente escanea `hubara_agency/src/plugins/<id>/` buscando llamadas
a `get_task_queue("plugin_id", "worker_name")` para detectar el patrón
canónico de invocación de workers. Cada match genera un edge
`api_router → worker` (`kind: invokes_worker`).

Diseño:
    - `build_system_graph(manifests_dir=None, code_dir=None)` es la entry-point.
    - Si paths son None, usa la convención del repo.
    - Idempotent dado mismos archivos.
    - NO efectos colaterales — solo retorna `SystemGraph`.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from src.plugins.system_map.domain.contracts import (
    CompletenessStatus,
    Edge,
    Node,
    PluginSummary,
    Stats,
    SystemGraph,
)
from src.plugins.system_map.domain.orphan_detector import detect_orphans

SCHEMA_VERSION = "0.2.0"


def _default_manifests_dir() -> Path:
    """Misma convención que `hubara_agency/src/main.py`."""
    # hubara_agency/src/plugins/system_map/domain/builder.py
    # → up 5 levels = repo root
    repo_root = Path(__file__).resolve().parents[5]
    return repo_root / "frontend_dashboard" / "src" / "plugins"


def _default_code_dir() -> Path:
    """Path al código Python de los plugins backend.

    builder.py vive en: hubara_agency/src/plugins/system_map/domain/builder.py
        parents[0] = .../domain/
        parents[1] = .../system_map/
        parents[2] = .../plugins/    ← queremos este (dir con todos los plugins)
        parents[3] = .../src/        (demasiado arriba)
    """
    return Path(__file__).resolve().parents[2]


# Regex para detectar invocación canónica:
#     get_task_queue("plugin_id", "worker_name")
#     get_task_queue('plugin_id', 'worker_name')
# Tolera spaces around quotes/commas. NO matchea variables (solo string literals).
_GET_TASK_QUEUE_RE = re.compile(
    r"""get_task_queue\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)""",
)


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
    has_frontend = "frontend" in manifest
    has_api = "api" in manifest
    has_agent = "agent" in manifest
    return Node(
        id=f"plugin:{plugin_id}",
        kind="plugin",
        plugin_id=plugin_id,
        label=manifest.get("display_name") or plugin_id,
        data={
            "version": manifest.get("version", "?"),
            "description": manifest.get("description") or "",
            "depends_on": list(manifest.get("depends_on") or []),
            "has_frontend": has_frontend,
            "has_api": has_api,
            "has_agent": has_agent,
            "completeness": _calculate_completeness(has_frontend, has_api, has_agent),
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


def _scan_workflow_invocations(
    code_dir: Path,
    plugin_id: str,
) -> list[tuple[str, str, str]]:
    """Escanea `<code_dir>/<plugin_id>/**/*.py` buscando `get_task_queue(...)`.

    Retorna [(source_file_relpath, target_plugin_id, worker_name)] por cada
    invocación detectada. NO falla si el plugin_id no existe en code_dir
    (puede ser un plugin frontend-only).
    """
    plugin_code = code_dir / plugin_id
    if not plugin_code.exists() or not plugin_code.is_dir():
        return []
    invocations: list[tuple[str, str, str]] = []
    for py in plugin_code.rglob("*.py"):
        # Skip __pycache__, tests del plugin
        if "__pycache__" in py.parts or "tests" in py.parts:
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for m in _GET_TASK_QUEUE_RE.finditer(text):
            target_plugin, worker_name = m.group(1), m.group(2)
            rel = str(py.relative_to(code_dir))
            invocations.append((rel, target_plugin, worker_name))
    return invocations


def _build_intra_plugin_edges(plugin_id: str, nodes_of_plugin: list[Node]) -> list[Edge]:
    """Edges implícitos lazy entre las contribuciones de un plugin.

    Heurísticas (NO requieren parsear código frontend — son razonables del
    manifest):
        - Si hay sidebars Y sections → cada sidebar `opens` cada section
        - Si hay sections Y api_routers → cada section `uses_api` cada api_router

    Cuando hay N×M productos cartesianos, los edges resultantes pueden ser
    ruidosos. Para V1 los emitimos todos — el frontend puede filtrar/colapsar.
    """
    sections = [n for n in nodes_of_plugin if n.kind == "section"]
    sidebars = [n for n in nodes_of_plugin if n.kind == "sidebar"]
    api_routers = [n for n in nodes_of_plugin if n.kind == "api_router"]
    edges: list[Edge] = []

    # sidebar → section ("opens")
    for sb in sidebars:
        for sc in sections:
            edges.append(
                Edge(
                    id=f"e:{sb.id}->{sc.id}",
                    source=sb.id,
                    target=sc.id,
                    kind="opens",
                    label=None,
                )
            )

    # section → api_router ("uses_api")
    for sc in sections:
        for ar in api_routers:
            edges.append(
                Edge(
                    id=f"e:{sc.id}->{ar.id}",
                    source=sc.id,
                    target=ar.id,
                    kind="uses_api",
                    label=None,
                )
            )

    return edges


def _calculate_completeness(
    has_frontend: bool, has_api: bool, has_agent: bool
) -> CompletenessStatus:
    """Categoría visual del plugin según qué layers contribuye.

    NO es orphan — `frontend_only` es un caso legítimo (plugins UI sobre
    entities/* compartidas). El frontend lo usa para colorear/badging.
    """
    if has_frontend and has_api and has_agent:
        return "complete"
    if has_frontend and has_api:
        return "frontend_api"
    if has_frontend and has_agent:
        return "frontend_agent"
    if has_api and has_agent:
        return "api_agent"
    if has_frontend:
        return "frontend_only"
    if has_api:
        return "api_only"
    if has_agent:
        return "agent_only"
    return "empty"


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


def build_system_graph(
    manifests_dir: Path | None = None,
    code_dir: Path | None = None,
) -> SystemGraph:
    """Construye el SystemGraph completo. Idempotent.

    Args:
        manifests_dir: override path de manifests YAML.
            None = convención del repo (`frontend_dashboard/src/plugins/`).
        code_dir: override path del código Python de plugins (para scan de
            `get_task_queue("plugin", "worker")`).
            None = convención (`hubara_agency/src/plugins/`).

    Returns:
        SystemGraph completo con nodos, edges, plugins, stats, warnings.
    """
    base_dir = manifests_dir or _default_manifests_dir()
    py_code_dir = code_dir or _default_code_dir()
    manifests, warnings = _load_manifests(base_dir)

    all_nodes: list[Node] = []
    all_edges: list[Edge] = []
    plugin_summaries: list[PluginSummary] = []
    all_plugin_ids = {pid for pid, _ in manifests}

    # Index global de workers para resolver edges api_router → worker
    # (necesario porque la invocación puede cruzar plugin boundary).
    worker_node_by_plugin_and_name: dict[tuple[str, str], str] = {}

    # Phase 1: construir nodos por plugin + edges intra-plugin lazy
    plugin_contributions: dict[str, list[Node]] = {}
    for plugin_id, manifest in manifests:
        plugin_node = _build_plugin_node(plugin_id, manifest)
        contributed: list[Node] = []
        contributed.extend(_build_frontend_nodes(plugin_id, manifest))
        contributed.extend(_build_api_nodes(plugin_id, manifest))
        contributed.extend(_build_agent_nodes(plugin_id, manifest))

        # Index workers para resolver `invokes_worker` edges después
        for n in contributed:
            if n.kind == "worker":
                worker_name = n.data.get("name", "")
                worker_node_by_plugin_and_name[(plugin_id, worker_name)] = n.id

        # Edges desde el plugin (contributes / exposes / depends_on / consumes_queue)
        all_edges.extend(_build_edges(plugin_node, contributed, all_plugin_ids))

        # Edges intra-plugin lazy (sidebar→section, section→api_router)
        all_edges.extend(_build_intra_plugin_edges(plugin_id, contributed))

        all_nodes.append(plugin_node)
        all_nodes.extend(contributed)
        plugin_contributions[plugin_id] = contributed

        plugin_summaries.append(
            PluginSummary(
                id=plugin_id,
                display_name=plugin_node.label,
                version=plugin_node.data.get("version", "?"),
                description=plugin_node.data.get("description") or None,
                has_frontend=plugin_node.data.get("has_frontend", False),
                has_api=plugin_node.data.get("has_api", False),
                has_agent=plugin_node.data.get("has_agent", False),
                completeness=_calculate_completeness(
                    plugin_node.data.get("has_frontend", False),
                    plugin_node.data.get("has_api", False),
                    plugin_node.data.get("has_agent", False),
                ),
                node_count=len([n for n in contributed if n.kind != "task_queue"]),
                orphan_count=0,
            )
        )

    # Phase 2: detectar invocaciones de workers desde código Python.
    # Match `get_task_queue("target_plugin", "worker_name")` y conecta:
    #   - source: el primer api_router del plugin donde está el archivo
    #     (si el archivo está en api/), o el agent module si está en agent/
    #   - target: worker en (target_plugin, worker_name) si existe
    #
    # Skipea el self-plugin `system_map` (su propio código contiene el regex
    # literal en docstrings — causaría falsos positivos en warnings).
    for plugin_id in plugin_contributions:
        if plugin_id == "system_map":
            continue
        invocations = _scan_workflow_invocations(py_code_dir, plugin_id)
        # Para cada invocation, asignar source heurístico:
        # si el archivo source está en `<plugin>/api/`, el source es el primer
        # api_router del plugin. Si está en `<plugin>/agent/`, el source es el
        # primer worker del propio plugin. Si está en `<plugin>/workers/`, skip
        # (es el worker registrándose, no invocación).
        for src_rel, target_plugin, worker_name in invocations:
            # Skip placeholders comunes que aparecen en docs/templates (no son
            # invocaciones reales): "plugin_id", "worker_name", "target_plugin", etc.
            if target_plugin in ("plugin_id", "target_plugin", "plugin") or \
               worker_name in ("worker_name", "worker", "worker_id"):
                continue
            target_id = worker_node_by_plugin_and_name.get(
                (target_plugin, worker_name)
            )
            if not target_id:
                # Solo warn si el target_plugin SÍ existe (es un bug real).
                # Si target_plugin tampoco existe, es probable código de docs
                # o feature en progreso.
                if target_plugin in all_plugin_ids:
                    warnings.append(
                        f"{plugin_id}: get_task_queue({target_plugin!r}, {worker_name!r}) "
                        f"en {src_rel} pero worker '{worker_name}' no está declarado en plugin '{target_plugin}'"
                    )
                continue

            # Determinar source según subdir del archivo
            parts = Path(src_rel).parts
            # parts[0] == plugin_id (carpeta del plugin)
            if len(parts) < 2:
                continue
            subdir = parts[1]
            source_id: str | None = None
            if subdir == "api":
                api_routers = [
                    n for n in plugin_contributions[plugin_id] if n.kind == "api_router"
                ]
                # match por slug: si el archivo es api/sales.py y hay un router
                # con id `api:<plugin>:sales`, preferir ese; sino primer router.
                file_slug = Path(src_rel).stem
                preferred = next(
                    (n for n in api_routers if n.id.endswith(f":{file_slug}")), None
                )
                source_id = preferred.id if preferred else (
                    api_routers[0].id if api_routers else None
                )
            elif subdir == "workers":
                # Es el worker registrándose en su task queue (worker.run() bootstrap),
                # no invocación cross. Skipear.
                continue
            elif subdir == "agent":
                # Agent code (e.g. use_case que arranca el workflow del worker).
                # Conceptualmente el flow es API → use_case → start_workflow(worker),
                # pero el grep solo ve el use_case → worker. Heurística:
                #   - Si el plugin tiene api_routers, asumir API es la entry y
                #     emitir edge desde el primer api_router (el flujo end-to-end
                #     es API → worker, no es perfecto pero útil visualmente).
                #   - Si no, edge desde el plugin container.
                api_routers = [
                    n for n in plugin_contributions[plugin_id] if n.kind == "api_router"
                ]
                if api_routers:
                    source_id = api_routers[0].id
                else:
                    source_id = f"plugin:{plugin_id}"

            if source_id and source_id != target_id:
                all_edges.append(
                    Edge(
                        id=f"e:{source_id}->invokes->{target_id}",
                        source=source_id,
                        target=target_id,
                        kind="invokes_worker",
                        label="start_workflow",
                    )
                )

    # Orphan detection
    all_nodes = detect_orphans(all_nodes, all_edges, all_plugin_ids, warnings)

    # Recompute orphan_count por plugin
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
            completeness=ps.completeness,
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
