"""Composition root del dashboard.

DI factories para los handlers HTTP del dashboard. Por ahora sólo cablea el
trío necesario para handoff humano:

  * `FilesystemMetadataStore`  — leer/escribir `metadata.json`
  * `FilesystemMessageHistoryStore` — append al JSONL
  * `get_temporal_client`      — terminar workflows / arrancar remarketing

Singleton por proceso (los stores son stateless). Si el dashboard crece a
otros endpoints que necesiten dependencias distintas, agregar factories aquí
en lugar de instanciar a mano en los handlers.
"""
from __future__ import annotations

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.session_history import FilesystemMessageHistoryStore
from src.platform.temporal.client import get_temporal_client
from src.platform.state import FilesystemMetadataStore


_METADATA_STORE: FilesystemMetadataStore | None = None
_HISTORY_STORE: FilesystemMessageHistoryStore | None = None


def get_metadata_store() -> FilesystemMetadataStore:
    global _METADATA_STORE
    if _METADATA_STORE is None:
        _METADATA_STORE = FilesystemMetadataStore(WORKSPACE_VAULT_DIR)
    return _METADATA_STORE


def get_history_store() -> FilesystemMessageHistoryStore:
    global _HISTORY_STORE
    if _HISTORY_STORE is None:
        _HISTORY_STORE = FilesystemMessageHistoryStore(WORKSPACE_VAULT_DIR)
    return _HISTORY_STORE


# Re-exporto `get_temporal_client` para que los handlers tengan un solo
# import root del que tomar las deps (`from src.dashboard.composition import ...`).
__all__ = ["get_metadata_store", "get_history_store", "get_temporal_client"]
