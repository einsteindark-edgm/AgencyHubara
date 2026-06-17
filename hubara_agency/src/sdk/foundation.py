"""Foundation — identidad, manifest, toggle y protocolos del plugin system.

Es la parte del SDK que TODO plugin usa: leer su manifest (SSoT post-PR11),
auto-gatearse por ``ENABLED_PLUGINS`` (INV-2), resolver rutas de conversación
(F6) y conformar los protocolos estructurales (F8).

Uso canónico::

    from src.sdk import get_task_queue, ensure_plugin_enabled

    async def main() -> None:
        ensure_plugin_enabled("eta")          # P-21: primera línea SIEMPRE
        queue = get_task_queue("eta", "eta")  # la queue vive en el manifest

Este módulo NO define lógica propia: re-exporta la implementación de
``src.platform.*`` con el idiom ``import X as X`` (PEP 484 re-export — además
evita que ``ruff --fix`` pode los imports). Si necesitás algo de platform que
no está acá, el camino es agregarlo AL SDK (con su check, regla de oro), no
importar platform directo (gate P-28).
"""
from __future__ import annotations

from src.platform.plugin_loader import (
    PluginDependencyError as PluginDependencyError,
    validate_enabled as validate_enabled,
)
from src.platform.plugin_manifest import (
    ManifestNotFoundError as ManifestNotFoundError,
    TaskQueueMissingError as TaskQueueMissingError,
    WorkerNotDeclaredError as WorkerNotDeclaredError,
    WorkflowClassNotDeclaredError as WorkflowClassNotDeclaredError,
    all_manifests as all_manifests,
    enabled_plugins as enabled_plugins,
    enumerate_manifest_workers as enumerate_manifest_workers,
    find_matching_transitions as find_matching_transitions,
    get_emitted_events as get_emitted_events,
    get_task_queue as get_task_queue,
    get_transitions as get_transitions,
    get_worker_spec as get_worker_spec,
    get_workflow_name as get_workflow_name,
    load_manifest as load_manifest,
)
from src.platform.plugin_protocol import (
    ApiModule as ApiModule,
    ConversationRouteOwner as ConversationRouteOwner,
    WorkerModule as WorkerModule,
)
from src.platform.plugin_runtime import (
    ensure_plugin_enabled as ensure_plugin_enabled,
)
from src.platform.routing import (
    ConversationRoute as ConversationRoute,
    RouteRegistryError as RouteRegistryError,
    conversation_routes as conversation_routes,
    resolve_route_workflow_id as resolve_route_workflow_id,
)
