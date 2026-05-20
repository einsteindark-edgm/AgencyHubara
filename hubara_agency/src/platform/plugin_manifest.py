"""Single source of truth para lectura de manifests de plugins.

PR11 — refactor "manifest = SSoT":

Pre-PR11, las task queues vivían hardcoded en ``src.platform.constants``
(`SALES_QUEUE`, `REMARKETING_QUEUE`, `CATALOG_SYNC_QUEUE`). Agregar un plugin
con worker nuevo requería editar ese archivo → conflict de merge cuando 2
agentes (Archon) trabajaban en plugins distintos en paralelo.

Post-PR11, cada worker declara su queue en ``agent.workers[].task_queue`` del
manifest. Este módulo lee el manifest y resuelve la queue por
``(plugin_id, worker_name)``. Resultado:

- Agregar un worker nuevo NO toca código compartido (solo el manifest del plugin).
- `constants.py` queda solo con rutas/prefijos cross-plugin
  (ROUTE_VENTAS, WHATSAPP_SESSION_PREFIX) que sí son globales.
- Tests invariantes (`tests/plugins/test_premortem_invariants.py`) verifican
  que todos los workers declarados tengan task_queue válido.

Cache: los manifests se leen una vez por process (``functools.cache``) — el
filesystem se toca al startup del worker o al startup del FastAPI loader.
"""
from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml


_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGINS_MANIFEST_DIR = _REPO_ROOT / "frontend_dashboard" / "src" / "plugins"


class ManifestNotFoundError(LookupError):
    """Lanzado cuando se pide manifest de un plugin que no existe."""


class WorkerNotDeclaredError(LookupError):
    """Lanzado cuando se pide info de un worker no listado en agent.workers[]."""


class TaskQueueMissingError(ValueError):
    """Lanzado cuando un worker existe en manifest pero no declara task_queue."""


class WorkflowClassNotDeclaredError(ValueError):
    """Lanzado cuando se pide un workflow_class no declarado en manifest.

    Causa típica (post-ADR-2026-05-20): se intenta arrancar un workflow cross-worker
    sin que el target haya declarado ``workflow_classes:`` en el manifest.
    El fix es agregarlo al manifest del worker target.
    """


@cache
def load_manifest(plugin_id: str) -> dict[str, Any]:
    """Devuelve el manifest parseado de ``plugin_id``.

    Cacheado por proceso. La primera invocación lee del filesystem; subsecuentes
    devuelven el mismo dict. **No mutar el resultado** — el cache lo comparte.
    """
    manifest_path = _PLUGINS_MANIFEST_DIR / plugin_id / "plugin.yaml"
    if not manifest_path.exists():
        raise ManifestNotFoundError(
            f"plugin manifest not found: {manifest_path} "
            f"(repo_root={_REPO_ROOT}, plugin_id={plugin_id!r})"
        )
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(manifest, dict):
        raise ManifestNotFoundError(
            f"plugin manifest malformed (not a dict): {manifest_path}"
        )
    return manifest


def get_worker_spec(plugin_id: str, worker_name: str) -> dict[str, Any]:
    """Devuelve el dict del worker en ``agent.workers[]`` matchando ``worker_name``."""
    manifest = load_manifest(plugin_id)
    agent_cfg = manifest.get("agent") or {}
    workers = agent_cfg.get("workers") or []
    for w in workers:
        if isinstance(w, dict) and w.get("name") == worker_name:
            return w
    raise WorkerNotDeclaredError(
        f"worker {plugin_id}/{worker_name} not found in manifest. "
        f"Declared workers: {[w.get('name') for w in workers if isinstance(w, dict)]}"
    )


def get_task_queue(plugin_id: str, worker_name: str) -> str:
    """Devuelve la Temporal task queue del worker desde el manifest.

    Esta es la API principal post-PR11 — los workers la usan en
    ``Worker(task_queue=...)`` y los dispatchers en ``start_workflow(task_queue=...)``.

    Raises ``TaskQueueMissingError`` si el worker existe pero no declara
    ``task_queue`` — fail-fast al startup, mejor que silencio en runtime.
    """
    spec = get_worker_spec(plugin_id, worker_name)
    queue = spec.get("task_queue")
    if not queue or not isinstance(queue, str):
        raise TaskQueueMissingError(
            f"worker {plugin_id}/{worker_name} missing `task_queue` in manifest "
            f"(got {queue!r}). Add `task_queue: queue-<name>` to "
            f"agent.workers[] entry."
        )
    return queue


def get_workflow_name(
    plugin_id: str,
    worker_name: str,
    index: int = 0,
) -> str:
    """Devuelve el nombre canónico de un workflow registrado por un worker.

    Lee de ``workers[name=worker_name].workflow_classes[index]`` en el manifest.
    Habilita el patrón "string-based dispatch" (ADR-2026-05-19, supersedido por
    ADR-2026-05-20): los callers cross-worker NO importan la clase Python del
    workflow target, sino que la resuelven por nombre via este helper.

    Args:
        plugin_id: id del plugin owner del worker.
        worker_name: ``agent.workers[].name`` del worker.
        index: si el worker registra >1 workflow, cuál usar (default 0).

    Raises:
        WorkflowClassNotDeclaredError: si el worker NO declara
            ``workflow_classes:`` o el index está fuera de rango.

    Example:
        >>> get_workflow_name("chats", "remarketing")
        'RemarketingWorkflow'
    """
    spec = get_worker_spec(plugin_id, worker_name)
    classes = spec.get("workflow_classes") or []
    if not classes:
        raise WorkflowClassNotDeclaredError(
            f"Worker {plugin_id}/{worker_name} has no workflow_classes "
            f"declared in manifest. Add it under "
            f"agent.workers[].workflow_classes: [<WorkflowClassName>, ...]"
        )
    if index >= len(classes):
        raise WorkflowClassNotDeclaredError(
            f"Worker {plugin_id}/{worker_name} declares only "
            f"{len(classes)} workflow_classes, requested index={index}."
        )
    return classes[index]


def get_emitted_events(plugin_id: str, worker_name: str) -> list[str]:
    """Devuelve los nombres de eventos que el worker declara emitir.

    Lee ``workers[].emits[]``. Si está vacío o ausente devuelve ``[]`` —
    significa que el worker es terminal o aún no migrado a Nivel 3.

    Usado por tests de consistencia: cada transition.on_event debe matchear
    un entry de emits[] del MISMO worker.
    """
    try:
        spec = get_worker_spec(plugin_id, worker_name)
    except WorkerNotDeclaredError:
        return []
    emits = spec.get("emits") or []
    return [str(e) for e in emits if isinstance(e, str)]


def get_transitions(plugin_id: str, worker_name: str) -> list["Transition"]:
    """Devuelve las transiciones declarativas del worker, ya parseadas a Transition.

    Lee ``workers[].transitions[]`` del manifest. Cada entry pasa por
    ``Transition.from_dict``, que valida required fields, normaliza defaults
    (target_plugin = source_plugin), y devuelve una instancia frozen.

    Devuelve lista vacía si el worker no tiene transitions (no es error —
    workflows terminales son válidos).

    Si el worker no existe (typo), propaga ``WorkerNotDeclaredError``.

    Cached por proceso via ``_load_transitions_for_worker`` (private impl).
    """
    return list(_load_transitions_for_worker(plugin_id, worker_name))


@cache
def _load_transitions_for_worker(
    plugin_id: str, worker_name: str
) -> tuple["Transition", ...]:
    # Local import: orchestration importa plugin_manifest para get_transitions;
    # plugin_manifest solo necesita Transition acá. Lazy + module-level cache.
    from src.platform.orchestration.transitions import Transition

    spec = get_worker_spec(plugin_id, worker_name)
    raw_transitions = spec.get("transitions") or []
    out: list[Transition] = []
    for entry in raw_transitions:
        if not isinstance(entry, dict):
            continue
        out.append(
            Transition.from_dict(
                entry,
                source_plugin=plugin_id,
                source_worker=worker_name,
            )
        )
    return tuple(out)


def find_matching_transitions(
    plugin_id: str, worker_name: str, envelope: "EventEnvelope"
) -> list["Transition"]:
    """Sugar: ``get_transitions(...) | filter(matches(envelope))``.

    Útil para tests y para callers que quieren ver matches sin ejecutar.
    El dispatcher real (``dispatch_event_activity``) hace lo mismo inline.
    """
    transitions = get_transitions(plugin_id, worker_name)
    return [t for t in transitions if t.matches(envelope)]


def enumerate_manifest_workers() -> list[tuple[str, str, str]]:
    """[(plugin_id, worker_name, module_path)] de TODOS los manifests del repo.

    Usado por tests invariantes (``test_premortem_invariants``) y por el
    meta-launcher (``src.run_workers``) — DRY: una sola implementación de
    discovery.

    Ignora filtro ``ENABLED_PLUGINS`` — los tests quieren ver todos.
    """
    if not _PLUGINS_MANIFEST_DIR.exists():
        return []
    out: list[tuple[str, str, str]] = []
    for plugin_dir in sorted(_PLUGINS_MANIFEST_DIR.iterdir()):
        if not plugin_dir.is_dir() or plugin_dir.name.startswith("_"):
            continue
        manifest_path = plugin_dir / "plugin.yaml"
        if not manifest_path.exists():
            continue
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(manifest, dict):
            continue
        agent_cfg = manifest.get("agent") or {}
        for w in agent_cfg.get("workers") or []:
            if not isinstance(w, dict):
                continue
            name = w.get("name")
            module = w.get("module")
            if isinstance(name, str) and isinstance(module, str):
                out.append((plugin_dir.name, name, module))
    return out
