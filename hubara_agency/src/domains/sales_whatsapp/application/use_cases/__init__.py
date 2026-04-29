"""Use cases del dominio Sales (entry points orquestados de aplicacion)."""
from __future__ import annotations

from src.domains.sales_whatsapp.application.use_cases.ingest_inbound_message import (
    IngestInboundMessage,
)
from src.domains.sales_whatsapp.application.use_cases.load_or_start_sales_session import (
    LoadOrStartSalesSession,
)

__all__ = [
    "IngestInboundMessage",
    "LoadOrStartSalesSession",
]
