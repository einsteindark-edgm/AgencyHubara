"""Adapter filesystem para `MetadataStorePort`.

Encapsula todo I/O de `metadata.json`: `Path.exists`, `Path.read_text`,
`Path.write_text`, `Path.mkdir`. Los use cases nunca tocan filesystem
directo (R-DIP — NEW-2 cerrado en F9).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class FilesystemMetadataStore:
    """Implementacion filesystem de `MetadataStorePort`.

    Cada sesion mapea a `<vault_dir>/<session_id>/metadata.json`. La lectura
    es tolerante a archivos corruptos (retorna `{}` ante `JSONDecodeError`),
    la escritura es atomica por simple `write_text` (mismo comportamiento que
    el legado en `service.py`).
    """

    def __init__(self, vault_dir: Path) -> None:
        self._vault_dir = vault_dir

    def _path_for(self, session_id: str) -> Path:
        return self._vault_dir / session_id / "metadata.json"

    def read(self, session_id: str) -> dict[str, Any]:
        path = self._path_for(session_id)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def write(self, session_id: str, data: dict[str, Any]) -> None:
        path = self._path_for(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
