"""Adapters de almacenamiento (filesystem) del dominio Sales.

Implementan los puertos de `application/ports/`. Cero llamadas a `Path.*`,
`open()`, `json.dump`, etc. fuera de este sub-paquete.
"""
from __future__ import annotations

from src.domains.sales_whatsapp.infrastructure.storage.filesystem_history_store import (
    FilesystemMessageHistoryStore,
)
from src.domains.sales_whatsapp.infrastructure.storage.filesystem_metadata_store import (
    FilesystemMetadataStore,
)

__all__ = [
    "FilesystemMessageHistoryStore",
    "FilesystemMetadataStore",
]
