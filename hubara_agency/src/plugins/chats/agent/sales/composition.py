"""Composition root del webhook handler de Sales (lado HTTP, NO worker).

Construye el grafo de deps que necesita el `IngestInboundMessage` use case:

* ``FilesystemMetadataStore(WORKSPACE_VAULT_DIR)``
* ``FilesystemMessageHistoryStore(WORKSPACE_VAULT_DIR)``
* ``WorkspaceConfig`` (sales runtime) -> ``get_workspace_path()`` (DEHA)
* ``client_factory`` -> ``get_temporal_client``

PR-D Sales: el `BrainLoaderPort` y la const `_SALES_BRAIN_DIR` ya no aplican
al path Sales — la identidad / tono / catalogo viven en `workspace/*.md` y se
leen via `ContextBuilder` durante `build_prompt`.

PR-D global cleanup (ADR-2026-05-06-10): tras la migracion DEHA workspace de
Remarketing (PR-A/PR-B remarketing), `RemarketingSessionWorkflow` tambien lee
desde su workspace canonico. Los `_REMARKETING_BRAIN_DIR` y
`remarketing_brain_loader` se eliminaron — `LoadOrStartSalesSession` ya no
los acepta. `BrainLoaderPort` y `DefaultBrainLoader` se neuterizaron (estan
pendientes de `git rm`).

PR-E: imports actualizados al layout lean — los stores de filesystem se
importan de ``state`` (modulo unificado top-level), los use cases de
``use_cases/`` (no mas ``application/use_cases/``).

Cachea la instancia del use case en proceso (la vault dir y el runtime
workspace path no cambian en runtime). Si en el futuro hace falta DI
sofisticada (FastAPI Depends, multi-tenant, etc.) este es el unico modulo
que cambia.
"""
from __future__ import annotations

import os

from exoclaw_temporal.config import WorkspaceConfig

from src.platform.analytics.composition import setup_analytics
from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.session_history import FilesystemMessageHistoryStore
from src.platform.state import FilesystemMetadataStore as _PlatformFsMetaStore  # noqa: F401
from src.platform.temporal.client import get_temporal_client
from src.platform.whatsapp.composition import get_current_rate_card
from src.plugins.chats.agent.sales.config.env import get_workspace_path
from src.plugins.chats.agent.sales.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.use_cases.ingest_delivery_status import (
    IngestDeliveryStatus,
)
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import (
    IngestInboundMessage,
)
from src.plugins.chats.agent.sales.use_cases.load_or_start_sales_session import (
    LoadOrStartSalesSession,
)


_INGEST_USE_CASE: IngestInboundMessage | None = None
_DELIVERY_STATUS_USE_CASE: IngestDeliveryStatus | None = None


def build_ingest_use_case() -> IngestInboundMessage:
    """Devuelve un singleton del `IngestInboundMessage` use case.

    Los stores son stateless (solo leen `WORKSPACE_VAULT_DIR`), asi que es
    seguro compartirlos entre requests. El `client_factory` se invoca por
    request para reaprovechar el patron actual de `get_temporal_client`.

    HU-002: arma también el `EventBus` analytics y lo inyecta para que el
    ingest emita eventos de referral CTWA + click tracking. Si las env
    `META_PIXEL_ID`/`META_CAPI_ACCESS_TOKEN` no están seteadas, solo se
    activa el sink filesystem (auditoría local) — Meta CAPI queda OFF.
    """
    global _INGEST_USE_CASE
    if _INGEST_USE_CASE is not None:
        return _INGEST_USE_CASE

    metadata_store = FilesystemMetadataStore(WORKSPACE_VAULT_DIR)
    history_store = FilesystemMessageHistoryStore(WORKSPACE_VAULT_DIR)

    # WorkspaceConfig canonico del agente Sales (donde viven IDENTITY.md,
    # SOUL.md, USER.md, TOOLS.md, AGENTS.md, memory/* y skills/*). Cruza el
    # workflow boundary como string en `SalesSessionInput.runtime_workspace_path`
    # y `bootstrap_sales_session_activity` lo consume para `WorkspaceConfig(path=...)`.
    sales_runtime_workspace = WorkspaceConfig(path=str(get_workspace_path()))

    load_session = LoadOrStartSalesSession(
        client_factory=get_temporal_client,
        metadata_store=metadata_store,
        sales_runtime_workspace=sales_runtime_workspace,
    )

    # Analytics bus singleton — filesystem siempre, Meta CAPI si hay token.
    event_bus = setup_analytics()
    tenant_id = os.getenv("HUBARA_TENANT_ID", "hubara")

    _INGEST_USE_CASE = IngestInboundMessage(
        history_store=history_store,
        load_session=load_session,
        metadata_store=metadata_store,
        event_bus=event_bus,
        tenant_id=tenant_id,
    )
    return _INGEST_USE_CASE


def build_ingest_delivery_status_use_case() -> IngestDeliveryStatus:
    """Singleton del `IngestDeliveryStatus` use case (HU-WA24H-001 F1.10).

    Consumido por el handler `statuses[]` del webhook FastAPI. Reusa el
    `MetadataStore` + `EventBus` que ya construye `build_ingest_use_case`,
    y agrega:

      * `RateCard` vigente (singleton del composition root WhatsApp).
      * `vault_dir` para escanear sesiones por wa_message_id.
      * Dead-letter path en `<vault>/_orphan_delivery_statuses.jsonl`.
    """
    global _DELIVERY_STATUS_USE_CASE
    if _DELIVERY_STATUS_USE_CASE is not None:
        return _DELIVERY_STATUS_USE_CASE

    metadata_store = FilesystemMetadataStore(WORKSPACE_VAULT_DIR)
    event_bus = setup_analytics()
    tenant_id = os.getenv("HUBARA_TENANT_ID", "hubara")

    _DELIVERY_STATUS_USE_CASE = IngestDeliveryStatus(
        metadata_store=metadata_store,
        rate_card=get_current_rate_card(),
        event_bus=event_bus,
        vault_dir=WORKSPACE_VAULT_DIR,
        tenant_id=tenant_id,
    )
    return _DELIVERY_STATUS_USE_CASE
