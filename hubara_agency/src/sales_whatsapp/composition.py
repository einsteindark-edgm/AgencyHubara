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

from exoclaw_temporal.config import WorkspaceConfig

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.temporal.client import get_temporal_client
from src.sales_whatsapp.config.env import get_workspace_path
from src.platform.session_history import FilesystemMessageHistoryStore
from src.sales_whatsapp.state import FilesystemMetadataStore
from src.sales_whatsapp.use_cases.ingest_inbound_message import (
    IngestInboundMessage,
)
from src.sales_whatsapp.use_cases.load_or_start_sales_session import (
    LoadOrStartSalesSession,
)


_INGEST_USE_CASE: IngestInboundMessage | None = None


def build_ingest_use_case() -> IngestInboundMessage:
    """Devuelve un singleton del `IngestInboundMessage` use case.

    Los stores son stateless (solo leen `WORKSPACE_VAULT_DIR`), asi que es
    seguro compartirlos entre requests. El `client_factory` se invoca por
    request para reaprovechar el patron actual de `get_temporal_client`.
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

    _INGEST_USE_CASE = IngestInboundMessage(
        history_store=history_store,
        load_session=load_session,
    )
    return _INGEST_USE_CASE
