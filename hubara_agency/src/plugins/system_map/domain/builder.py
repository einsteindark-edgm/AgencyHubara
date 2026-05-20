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
    """UN `frontend_unit` por plugin con frontend, conteniendo sub-data de
    sections + sidebars.

    Si el plugin NO declara frontend, retorna [].
    El frontend_unit tiene `data` con:
        - `sections`: list[{key, label, order, icon}]
        - `sidebars`: list[{route, label, icon}]
        - `entry`: ruta al index del frontend
        - `has_sections`: bool — al menos 1 section
        - `has_sidebars`: bool — al menos 1 sidebar
        - `is_complete`: bool — tiene ambas mitades

    El orphan_detector pinta `frontend_incomplete` si XOR de ambas mitades.
    """
    fe = manifest.get("frontend")
    if not fe:
        return []

    contributes = fe.get("contributes") or {}
    raw_sections = contributes.get("sections") or []
    raw_sidebars = contributes.get("sidebar") or []

    sections_data = [
        {
            "key": s.get("key", f"unnamed-{i}"),
            "label": s.get("label") or s.get("key", f"unnamed-{i}"),
            "order": s.get("order"),
            "icon": s.get("icon"),
        }
        for i, s in enumerate(raw_sections)
        if isinstance(s, dict)
    ]
    sidebars_data = [
        {
            "route": s.get("route", f"/unnamed-{i}"),
            "label": s.get("label") or s.get("route", f"/unnamed-{i}"),
            "icon": s.get("icon"),
        }
        for i, s in enumerate(raw_sidebars)
        if isinstance(s, dict)
    ]

    has_sections = len(sections_data) > 0
    has_sidebars = len(sidebars_data) > 0

    # Label corto: usa el del primer item disponible
    if sections_data:
        label = sections_data[0]["label"]
    elif sidebars_data:
        label = sidebars_data[0]["label"]
    else:
        label = "Frontend"

    return [
        Node(
            id=f"frontend:{plugin_id}",
            kind="frontend_unit",
            plugin_id=plugin_id,
            label=label,
            data={
                "entry": fe.get("entry", "./frontend"),
                "sections": sections_data,
                "sidebars": sidebars_data,
                "has_sections": has_sections,
                "has_sidebars": has_sidebars,
                "is_complete": has_sections and has_sidebars,
            },
        )
    ]


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
    """Workers Temporal — cada uno con su task_queue, `invokes` y `transitions`.

    ADR-2026-05-20: las `transitions:` declarativas son la SSoT para el flujo
    cross-worker. Las legacy `invokes:` se mantienen como compat (deprecadas)
    pero el system_map prefiere `transitions:` para edges si están presentes.
    """
    agent = manifest.get("agent") or {}
    workers = agent.get("workers") or []
    nodes: list[Node] = []
    task_queues_seen: set[str] = set()

    for idx, worker in enumerate(workers):
        if not isinstance(worker, dict):
            continue
        name = worker.get("name") or f"worker-{idx}"
        task_queue = worker.get("task_queue") or ""
        invokes_raw = worker.get("invokes") or []
        invokes_data = [
            {
                "plugin": inv.get("plugin", plugin_id),  # default = mismo plugin
                "worker": inv.get("worker"),
                "via": inv.get("via", "start_workflow"),
                "when": inv.get("when"),
            }
            for inv in invokes_raw
            if isinstance(inv, dict) and inv.get("worker")
        ]
        # ADR-2026-05-20: leer transitions[] declarativas como SSoT del flujo.
        transitions_raw = worker.get("transitions") or []
        transitions_data = []
        for t in transitions_raw:
            if not isinstance(t, dict):
                continue
            action = t.get("action") or {}
            transitions_data.append(
                {
                    "id": t.get("id"),
                    "on_event": t.get("on_event"),
                    "when": dict(t.get("when") or {}),
                    "target_plugin": action.get("target_plugin") or plugin_id,
                    "target_worker": action.get("target_worker"),
                    "target_workflow": action.get("target_workflow"),
                    "via": action.get("via"),
                }
            )
        emits_data = [e for e in (worker.get("emits") or []) if isinstance(e, str)]
        workflow_classes_data = [
            wc for wc in (worker.get("workflow_classes") or []) if isinstance(wc, str)
        ]
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
                    "invokes": invokes_data,
                    "transitions": transitions_data,
                    "emits": emits_data,
                    "workflow_classes": workflow_classes_data,
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
    """Edge lazy: frontend_unit → api_router (mismo plugin).

    Asume que la UI del plugin consume el(los) API del propio plugin. No es
    verificación de código — es razonable del manifest.
    """
    frontend_units = [n for n in nodes_of_plugin if n.kind == "frontend_unit"]
    api_routers = [n for n in nodes_of_plugin if n.kind == "api_router"]
    edges: list[Edge] = []

    for fu in frontend_units:
        for ar in api_routers:
            edges.append(
                Edge(
                    id=f"e:{fu.id}->{ar.id}",
                    source=fu.id,
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
    """Edges del plugin: pertenencia (muted) + funcionales.

    Emite:
        - `belongs_to` (plugin → child) — pertenencia muda, gris punteado. Para
          que el grafo muestre visualmente que cada nodo pertenece a su plugin,
          incluso cuando no tiene conexión funcional con nadie (workers sin
          invocador, etc.). Excluye task_queue (es shared y se conecta desde
          worker via `consumes_queue`).
        - `consumes_queue` (worker → task_queue) — funcional.
        - `depends_on` (plugin → plugin) — funcional, declarada en manifest.
    """
    edges: list[Edge] = []

    # plugin → child (pertenencia muda)
    for n in contributed_nodes:
        if n.kind == "task_queue":
            continue  # task_queue es shared (no es contribución exclusiva)
        edges.append(
            Edge(
                id=f"e:{plugin_node.id}->belongs->{n.id}",
                source=plugin_node.id,
                target=n.id,
                kind="belongs_to",
            )
        )

    # worker → task_queue (funcional)
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

    # plugin.depends_on → otro plugin (funcional)
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

    return edges


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

    # Phase 1.5: leer flujo cross-worker del manifest.
    # SSoT post-ADR-2026-05-20: `transitions[]` declarativas. Si un worker
    # tiene transitions, IGNORAMOS sus `invokes[]` legacy (evita edges dupes
    # cuando ambos describen el mismo flujo). Si solo tiene invokes (Nivel 1
    # legacy), las leemos.
    declared_invocations: set[tuple[str, str]] = set()  # (source_worker_id, target_worker_id)
    declared_meta: dict[tuple[str, str], dict] = {}     # id pair → {via, when, plugin, label}
    for plugin_id in plugin_contributions:
        for n in plugin_contributions[plugin_id]:
            if n.kind != "worker":
                continue
            source_id = n.id

            # Si el worker declara transitions[] (Nivel 3), usar ESAS.
            transitions = n.data.get("transitions", [])
            if transitions:
                for t in transitions:
                    target_plugin = t.get("target_plugin") or plugin_id
                    target_worker = t.get("target_worker")
                    target_id = worker_node_by_plugin_and_name.get(
                        (target_plugin, target_worker)
                    )
                    if not target_id:
                        warnings.append(
                            f"{plugin_id}.workers[{n.data['name']}].transitions[{t.get('id')}] "
                            f"targets {target_plugin}.{target_worker} but no such worker exists"
                        )
                        continue
                    pair = (source_id, target_id)
                    declared_invocations.add(pair)
                    # Label rico: id + when summary + via
                    when_summary = ""
                    if t.get("when"):
                        when_summary = " " + ",".join(
                            f"{k}={v}" for k, v in (t.get("when") or {}).items()
                        )
                    label = f"{t.get('on_event','event')}{when_summary} → {t.get('via','start')}"
                    declared_meta[pair] = {
                        "via": t.get("via", "start_workflow"),
                        "when": t.get("when"),
                        "source": "manifest_transitions",
                        "label": label,
                    }
                continue  # transitions ganan, no leer invokes

            # Fallback Nivel 1: leer invokes[] legacy.
            for inv in n.data.get("invokes", []):
                target_plugin = inv.get("plugin") or plugin_id
                target_worker = inv.get("worker")
                target_id = worker_node_by_plugin_and_name.get(
                    (target_plugin, target_worker)
                )
                if not target_id:
                    warnings.append(
                        f"{plugin_id}.workers[{n.data['name']}].invokes declara "
                        f"{target_plugin}.{target_worker} pero ese worker no existe"
                    )
                    continue
                pair = (source_id, target_id)
                declared_invocations.add(pair)
                declared_meta[pair] = {
                    "via": inv.get("via", "start_workflow"),
                    "when": inv.get("when"),
                    "source": "manifest_invokes",
                    "label": inv.get("via", "invokes"),
                }

    # Emitir edges declarados (sin importar si código los confirma)
    for (src, tgt), meta in declared_meta.items():
        all_edges.append(
            Edge(
                id=f"e:{src}->declared->{tgt}",
                source=src,
                target=tgt,
                kind="invokes_worker",
                label=meta.get("label") or meta["via"],
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
                # Skipear self-references: cuando el agent del MISMO plugin
                # se referencia a sí mismo (catalog/agent/workflows/sync.py
                # llamando worker `catalog:sync` es un child workflow / continue,
                # no invocación cross-component). Solo emitir si target ≠ self
                # O si hay un api_router como entry point legítimo.
                api_routers = [
                    n for n in plugin_contributions[plugin_id] if n.kind == "api_router"
                ]
                if api_routers:
                    # Flow end-to-end: API → use_case → start_workflow(worker)
                    source_id = api_routers[0].id
                elif target_plugin != plugin_id:
                    # Cross-plugin invocation desde agent (e.g. catalog agent
                    # invoca worker de otro plugin). Source = plugin container.
                    source_id = f"plugin:{plugin_id}"
                else:
                    # Self-reference dentro del agent del mismo plugin sin API.
                    # NO emitir edge — no aporta info nueva.
                    continue

            if source_id and source_id != target_id:
                # Dedupe vs declared_invocations: si el manifest ya declara este
                # par, no emitimos edge duplicado (el manifest gana visualmente).
                # Solo emitimos si source es WORKER (entonces compara directamente)
                # — para sources tipo api_router, son siempre del scan (no del
                # manifest, que es worker→worker).
                code_pair = (source_id, target_id)
                if source_id.startswith("worker:") and code_pair in declared_invocations:
                    continue  # ya declarado en manifest, evita duplicado

                # Si el source es worker y el código tiene invocación pero el
                # manifest del source-worker NO la declara → warn de drift.
                if source_id.startswith("worker:") and code_pair not in declared_invocations:
                    warnings.append(
                        f"DRIFT: código en {src_rel} invoca {target_plugin}.{worker_name} "
                        f"desde worker {source_id}, pero el manifest no lo declara en "
                        f"`workers[].invokes` — agregalo para SSoT"
                    )

                all_edges.append(
                    Edge(
                        id=f"e:{source_id}->scan->{target_id}",
                        source=source_id,
                        target=target_id,
                        kind="invokes_worker",
                        label="code scan",
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
