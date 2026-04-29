"""Port: MetadataStorePort.

Describe la capability de leer/escribir el `metadata.json` por sesion. La
implementacion default vive en `infrastructure/storage/filesystem_metadata_store.py`
y opera sobre `WORKSPACE_VAULT_DIR / <session_id> / metadata.json`.

Los use cases del webhook handler dependen de este Protocol — nunca tocan
filesystem directo (NEW-2 cerrado en F9).
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MetadataStorePort(Protocol):
    """Lectura/escritura del documento de metadatos por sesion."""

    def read(self, session_id: str) -> dict[str, Any]:
        """Lee el metadata de la sesion. Retorna `{}` si no existe o esta corrupto."""
        ...

    def write(self, session_id: str, data: dict[str, Any]) -> None:
        """Persiste el metadata de la sesion (crea el directorio si hace falta)."""
        ...
