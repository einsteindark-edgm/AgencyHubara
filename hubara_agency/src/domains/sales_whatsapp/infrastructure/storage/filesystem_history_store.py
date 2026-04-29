"""Adapter filesystem para `MessageHistoryStorePort`.

Encapsula todo I/O de `<session>.jsonl`: `mkdir`, `open(append)`, `f.write`.
Los use cases nunca tocan filesystem directo (NEW-2 cerrado en F9).
"""
from __future__ import annotations

import json
from pathlib import Path


class FilesystemMessageHistoryStore:
    """Implementacion filesystem de `MessageHistoryStorePort`.

    Cada sesion mapea a `<vault_dir>/<session_id>/sessions/<session_id>.jsonl`.
    Cada llamada a `append_user_event` agrega una linea JSON serializada con
    `ensure_ascii=False` (mismo shape que el legado en `service.py`).
    """

    def __init__(self, vault_dir: Path) -> None:
        self._vault_dir = vault_dir

    def _path_for(self, session_id: str) -> Path:
        return self._vault_dir / session_id / "sessions" / f"{session_id}.jsonl"

    def append_user_event(self, session_id: str, content: str) -> None:
        path = self._path_for(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        event = {"role": "user", "content": content}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
