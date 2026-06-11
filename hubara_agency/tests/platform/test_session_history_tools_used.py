"""`tools_used` en el JSONL de sesión — evidencia de tools para el juez.

Caso ep_010 (run fa1eb974): el juez puntuó "no llamó search_products / no
escaló" cuando la history de Temporal probaba lo contrario — las tools corrían
pero el JSONL no las registraba. El workflow ahora pasa `result.tools_used` y
el store las persiste en un campo NUEVO (no `tool_calls`: ese cambia el
`ui_type` del dashboard a `agent_tool_call` y rompería el render de burbujas).
"""
from __future__ import annotations

import json
from pathlib import Path

from src.platform.session_history.store import FilesystemMessageHistoryStore


def _events(vault: Path, session_id: str) -> list[dict]:
    path = vault / session_id / "sessions" / f"{session_id}.jsonl"
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def test_append_assistant_event_persists_tools_used(tmp_path: Path):
    store = FilesystemMessageHistoryStore(tmp_path)
    store.append_assistant_event(
        "wa_x", "Listo, tu pedido quedó registrado.",
        tools_used=["register_order", "manage_conversation_tag", "escalate_to_human"],
    )
    (event,) = _events(tmp_path, "wa_x")
    assert event["tools_used"] == [
        "register_order", "manage_conversation_tag", "escalate_to_human",
    ]
    # NO se confunde con tool_calls (clasificador del dashboard intacto).
    assert "tool_calls" not in event


def test_append_assistant_event_omits_empty_tools(tmp_path: Path):
    store = FilesystemMessageHistoryStore(tmp_path)
    store.append_assistant_event("wa_x", "Hola.", tools_used=[])
    store.append_assistant_event("wa_x", "Sigo acá.")
    for event in _events(tmp_path, "wa_x"):
        assert "tools_used" not in event
