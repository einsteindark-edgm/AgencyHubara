"""Composition root del webhook handler de Sales (lado HTTP, NO worker).

Construye el grafo de deps que necesita el `IngestInboundMessage` use case:

* `MetadataStorePort` -> `FilesystemMetadataStore(WORKSPACE_VAULT_DIR)`
* `MessageHistoryStorePort` -> `FilesystemMessageHistoryStore(WORKSPACE_VAULT_DIR)`
* `BrainLoaderPort` (sales + remarketing) -> `DefaultBrainLoader`
* `client_factory` -> `get_temporal_client`

Cachea la instancia del use case en proceso (la vault dir y los brain dirs no
cambian en runtime). Si en el futuro hace falta DI sofisticada (FastAPI Depends,
multi-tenant, etc.) este es el unico modulo que cambia.
"""
from __future__ import annotations

from pathlib import Path

from src.core.config import WORKSPACE_VAULT_DIR
from src.core.infrastructure.brains import DefaultBrainLoader
from src.core.temporal_client import get_temporal_client
from src.domains.sales_whatsapp.application.use_cases.ingest_inbound_message import (
    IngestInboundMessage,
)
from src.domains.sales_whatsapp.application.use_cases.load_or_start_sales_session import (
    LoadOrStartSalesSession,
)
from src.domains.sales_whatsapp.infrastructure.storage import (
    FilesystemMessageHistoryStore,
    FilesystemMetadataStore,
)

_SALES_BRAIN_DIR = Path(__file__).parent / "shared_brain"
_REMARKETING_BRAIN_DIR = (
    Path(__file__).parent.parent / "remarketing_whatsapp" / "shared_brain"
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

    sales_brain_loader = DefaultBrainLoader()
    remarketing_brain_loader = DefaultBrainLoader()

    load_session = LoadOrStartSalesSession(
        client_factory=get_temporal_client,
        metadata_store=metadata_store,
        sales_brain_loader=sales_brain_loader,
        remarketing_brain_loader=remarketing_brain_loader,
        sales_brain_dir=_SALES_BRAIN_DIR,
        remarketing_brain_dir=_REMARKETING_BRAIN_DIR,
    )

    _INGEST_USE_CASE = IngestInboundMessage(
        history_store=history_store,
        load_session=load_session,
    )
    return _INGEST_USE_CASE
