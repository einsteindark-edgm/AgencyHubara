"""Hubara SDK — la superficie pública de la plataforma para plugins.

Modelo mental (estilo Apple Foundation/Kits): un plugin importa SOLO de
``src.sdk`` e implementa protocolos; ``src.platform.*`` es implementación
PRIVADA de la plataforma y puede refactorizarse sin romper plugins. La
dirección de dependencias es ``plugins → sdk → platform`` (contratos en
``.importlinter``; el ratchet P-28 congela los imports legacy directos).

Organización (importá el kit que necesitás — no hay God-module):

- ``src.sdk`` (este módulo)  → Foundation: manifest, toggle, protocolos.
- ``src.sdk.runtime``        → estado, vault, Temporal client, heartbeat, logging.
- ``src.sdk.eventkit``       → eventos declarativos + dispatcher (canal 2).
- ``src.sdk.agentkit``       → workers conversacionales (turn loop, tools, registries).
- ``src.sdk.connectorkit``   → ports de capability hacia vendors externos (F-SDK-4).
- ``src.sdk.testkit``        → TCK: suite de conformance + certificación (F-SDK-2).
- ``src.sdk.cli``            → ``uv run python -m src.sdk.cli`` (F-SDK-3).

Regla de oro: ningún símbolo entra al SDK sin (a) su check en el TestKit,
(b) su template/CLI, (c) su representación en el catálogo. Ver
``src/sdk/CLAUDE.md`` y ``docs/_sdk/`` antes de extender.
"""
from __future__ import annotations

from src.sdk.diagnostics import (
    Diagnostic as Diagnostic,
    format_diagnostic as format_diagnostic,
    get_diagnostic as get_diagnostic,
)
from src.sdk.foundation import (
    ApiModule as ApiModule,
    ConversationRoute as ConversationRoute,
    ConversationRouteOwner as ConversationRouteOwner,
    ManifestNotFoundError as ManifestNotFoundError,
    PluginDependencyError as PluginDependencyError,
    RouteRegistryError as RouteRegistryError,
    TaskQueueMissingError as TaskQueueMissingError,
    WorkerModule as WorkerModule,
    WorkerNotDeclaredError as WorkerNotDeclaredError,
    WorkflowClassNotDeclaredError as WorkflowClassNotDeclaredError,
    all_manifests as all_manifests,
    conversation_routes as conversation_routes,
    enabled_plugins as enabled_plugins,
    ensure_plugin_enabled as ensure_plugin_enabled,
    enumerate_manifest_workers as enumerate_manifest_workers,
    get_emitted_events as get_emitted_events,
    get_task_queue as get_task_queue,
    get_transitions as get_transitions,
    get_worker_spec as get_worker_spec,
    get_workflow_name as get_workflow_name,
    load_manifest as load_manifest,
    resolve_route_workflow_id as resolve_route_workflow_id,
    validate_enabled as validate_enabled,
)
from src.sdk.manifest_model import (
    ARCHETYPES as ARCHETYPES,
    Archetype as Archetype,
    ManifestValidationError as ManifestValidationError,
    PluginManifest as PluginManifest,
    all_typed_manifests as all_typed_manifests,
    load_typed_manifest as load_typed_manifest,
    parse_manifest as parse_manifest,
)
