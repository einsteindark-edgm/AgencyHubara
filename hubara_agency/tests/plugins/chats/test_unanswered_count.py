"""`unanswered_count` en `/api/dashboard/sessions` — chats en espera de respuesta.

Como el contador verde de WhatsApp: cuántos mensajes del CLIENTE quedaron
después de la última respuesta que él VIO (del bot o del operador). 0 = nadie
le debe nada. Un turno del bot que solo llama tools (sin texto) no es una
respuesta — el cliente no recibió nada; un envío no-textual (catálogo,
botones: `kind: "ui_component"`) sí lo es.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.chats.api import dashboard


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    app = FastAPI()
    app.include_router(dashboard.router, prefix="/api/dashboard")
    monkeypatch.setattr(dashboard, "_resolve_ad_names", lambda ids: {})
    with patch("src.plugins.chats.api.dashboard.WORKSPACE_VAULT_DIR", tmp_path):
        yield TestClient(app), tmp_path


def _seed(vault: Path, sid: str, events: list[dict]) -> None:
    hist = vault / sid / "sessions" / f"{sid}.jsonl"
    hist.parent.mkdir(parents=True, exist_ok=True)
    hist.write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
        encoding="utf-8",
    )


def _count(c: TestClient, sid: str) -> int:
    body = c.get("/api/dashboard/sessions").json()
    return {s["session_id"]: s for s in body["sessions"]}[sid]["unanswered_count"]


def test_counts_customer_messages_after_the_last_reply(client) -> None:
    c, vault = client
    _seed(vault, "wa_573000000001", [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "¡Hola!"},
        {"role": "user", "content": "precio?"},
        {"role": "user", "content": "y envío?"},
        {"role": "user", "content": "??"},
    ])
    assert _count(c, "wa_573000000001") == 3


def test_answered_chat_counts_zero_bot_or_human(client) -> None:
    c, vault = client
    _seed(vault, "wa_573000000002", [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "sender": "human", "content": "te ayudo"},
    ])
    _seed(vault, "wa_573000000003", [
        {"role": "user", "content": "catálogo"},
        {"role": "assistant", "kind": "ui_component", "content": "[catálogo enviado]"},
    ])
    (vault / "wa_573000000004").mkdir()  # sin historial
    assert _count(c, "wa_573000000002") == 0
    assert _count(c, "wa_573000000003") == 0
    assert _count(c, "wa_573000000004") == 0


def test_tool_only_turns_are_not_a_reply(client) -> None:
    c, vault = client
    _seed(vault, "wa_573000000005", [
        {"role": "assistant", "content": "¡Hola!"},
        {"role": "user", "content": "quiero uno"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "{}"},
        {"role": "assistant", "content": "pensando…", "tool_calls": [{"id": "2"}]},
    ])
    assert _count(c, "wa_573000000005") == 1
