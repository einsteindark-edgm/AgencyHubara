"""Adapter filesystem del log append-only de mensajes por sesion.

Persiste dos shapes de evento en ``<vault_dir>/<session_id>/sessions/<session_id>.jsonl``:

  * ``append_user_event(session_id, content)`` →
    ``{"role": "user", "content": ...}``
  * ``append_assistant_event(session_id, content, tool_calls=None)`` →
    ``{"role": "assistant", "content": ..., "timestamp": "<ISO>"}``
    (``tool_calls`` se incluye solo si no es None ni vacio)

Ambos serializan con ``ensure_ascii=False`` para preservar caracteres
no-ASCII (mismo shape que el legado en ``service.py``).

El clasificador del dashboard (``api.py::get_session_detail``) deriva el
campo ``ui_type`` a partir de ``role`` y ``tool_calls`` — no es necesario
persistirlo aca.

Vive en ``src.platform.session_history`` porque tanto ``sales_whatsapp`` como
``remarketing_whatsapp`` lo necesitan (R-DIP #10).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class FilesystemMessageHistoryStore:
    """Adapter filesystem del log append-only por sesion."""

    def __init__(self, vault_dir: Path) -> None:
        self._vault_dir = vault_dir

    def _path_for(self, session_id: str) -> Path:
        return self._vault_dir / session_id / "sessions" / f"{session_id}.jsonl"

    def _append(self, session_id: str, event: dict[str, Any]) -> None:
        path = self._path_for(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def append_user_event(self, session_id: str, content: str) -> None:
        self._append(session_id, {"role": "user", "content": content})

    def append_assistant_event(
        self,
        session_id: str,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "role": "assistant",
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if tool_calls:
            event["tool_calls"] = tool_calls
        self._append(session_id, event)
