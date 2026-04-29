"""Puertos (typing.Protocol) consumidos por los use cases del dominio Sales.

Cada Protocol describe una capability hacia infraestructura externa
(filesystem, etc.). Los use cases dependen de estas abstracciones; los
adapters concretos viven en `infrastructure/storage/` y `infrastructure/...`
y los inyecta el composition root.
"""
from __future__ import annotations

from src.domains.sales_whatsapp.application.ports.message_history_store import (
    MessageHistoryStorePort,
)
from src.domains.sales_whatsapp.application.ports.metadata_store import (
    MetadataStorePort,
)

__all__ = [
    "MessageHistoryStorePort",
    "MetadataStorePort",
]
