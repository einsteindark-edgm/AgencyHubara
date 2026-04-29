"""Port: MessageHistoryStorePort.

Describe la capability de append-only sobre el JSONL de history del usuario.
El use case `IngestInboundMessage` lo invoca como primer paso (asegurar que el
mensaje del cliente queda persistido antes de ir a memoria viva en Temporal).

La implementacion default vive en `infrastructure/storage/filesystem_history_store.py`
y opera sobre `WORKSPACE_VAULT_DIR / <session_id> / sessions / <session_id>.jsonl`.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class MessageHistoryStorePort(Protocol):
    """Append-only de eventos de conversacion del lado usuario."""

    def append_user_event(self, session_id: str, content: str) -> None:
        """Agrega una linea JSONL `{"role": "user", "content": ...}` al log."""
        ...
