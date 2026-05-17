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
