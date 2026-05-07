"""Use cases del dominio Sales (entry points orquestados de aplicacion).

PR-E: movido de ``application/use_cases/`` (hexagonal) a ``use_cases/``
(top-level). A esta escala (1.3K LoC, 2 use cases concretos) la carpeta
``application/`` no aporta indireccion utilizable — un sub-folder al lado del
resto del dominio es mas claro de descubrir.
"""
from __future__ import annotations

from src.sales_whatsapp.use_cases.ingest_inbound_message import (
    IngestInboundMessage,
)
from src.sales_whatsapp.use_cases.load_or_start_sales_session import (
    LoadOrStartSalesSession,
)

__all__ = [
    "IngestInboundMessage",
    "LoadOrStartSalesSession",
]
