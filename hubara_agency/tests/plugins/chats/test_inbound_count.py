"""`inbound_count` en `/api/dashboard/sessions` — base del contador "no vistos".

Total de mensajes del CLIENTE en el historial. El dashboard recuerda cuántos
había cuando el operador abrió el chat y pinta la diferencia, como WhatsApp:
lo que decide es si el operador ABRIÓ la conversación, no quién habló último
(un "gracias, hasta luego" del cliente al cierre también es un no visto).
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


def _seed(vault: Path, sid: str, lines: list[str]) -> None:
    hist = vault / sid / "sessions" / f"{sid}.jsonl"
    hist.parent.mkdir(parents=True, exist_ok=True)
    hist.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def _count(c: TestClient, sid: str) -> int:
    body = c.get("/api/dashboard/sessions").json()
    return {s["session_id"]: s for s in body["sessions"]}[sid]["inbound_count"]


def test_counts_every_customer_message_even_after_the_bot_replied(client) -> None:
    c, vault = client
    ev = lambda role, text: json.dumps({"role": role, "content": text})  # noqa: E731
    _seed(vault, "wa_573000000001", [
        ev("user", "hola"),
        ev("assistant", "¡Hola!"),
        ev("user", "precio?"),
        ev("assistant", "Cuesta…"),
        ev("user", "gracias, hasta luego"),
        ev("tool", "{}"),
    ])
    assert _count(c, "wa_573000000001") == 3


def test_zero_without_history_and_skips_corrupt_lines(client) -> None:
    c, vault = client
    (vault / "wa_573000000002").mkdir()
    _seed(vault, "wa_573000000003", [
        json.dumps({"role": "user", "content": "a"}),
        '{"role": "user", "content": "cort',  # escritura a medias
        json.dumps({"role": "assistant", "content": 'dijo "user"'}),
    ])
    assert _count(c, "wa_573000000002") == 0
    assert _count(c, "wa_573000000003") == 1
