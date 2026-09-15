"""`last_inbound_ms` en `/api/dashboard/sessions` — el sonido de "mensaje nuevo".

El dashboard necesita saber cuándo ESCRIBIÓ EL CLIENTE, no cuándo cambió la
sesión: `last_updated_timestamp` es el mtime del JSONL y se mueve también con
cada respuesta del bot o del operador — sonar con eso sería un "ding" por cada
turno propio. `last_inbound_ms` = epoch ms del último evento `role: "user"`
del historial (lo escribe `FilesystemMessageHistoryStore.append_user_event`
con timestamp ISO UTC).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
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


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _seed_history(vault: Path, sid: str, events: list[dict]) -> None:
    hist = vault / sid / "sessions" / f"{sid}.jsonl"
    hist.parent.mkdir(parents=True, exist_ok=True)
    hist.write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
        encoding="utf-8",
    )


def _by_id(c: TestClient) -> dict[str, dict]:
    body = c.get("/api/dashboard/sessions").json()
    return {s["session_id"]: s for s in body["sessions"]}


def test_last_inbound_is_the_last_customer_message_not_the_bot_reply(client) -> None:
    c, vault = client
    _seed_history(vault, "wa_573000000001", [
        {"role": "user", "content": "hola", "timestamp": _iso(1_789_000_000_000)},
        {"role": "assistant", "content": "¡Hola!", "timestamp": _iso(1_789_000_001_000)},
        {"role": "user", "content": "precio?", "timestamp": _iso(1_789_000_060_000)},
        {"role": "assistant", "content": "Cuesta…", "timestamp": _iso(1_789_000_065_000)},
        {"role": "assistant", "sender": "human", "content": "te ayudo", "timestamp": _iso(1_789_000_070_000)},
    ])

    assert _by_id(c)["wa_573000000001"]["last_inbound_ms"] == 1_789_000_060_000


def test_last_inbound_is_null_without_customer_messages(client) -> None:
    c, vault = client
    _seed_history(vault, "wa_573000000002", [
        {"role": "assistant", "content": "plantilla", "timestamp": _iso(1_789_000_000_000)},
    ])
    (vault / "wa_573000000003").mkdir()  # sesión sin JSONL todavía

    sessions = _by_id(c)
    assert sessions["wa_573000000002"]["last_inbound_ms"] is None
    assert sessions["wa_573000000003"]["last_inbound_ms"] is None


def test_last_inbound_found_behind_a_long_tail_of_assistant_turns(client) -> None:
    """El historial se lee desde el final por bloques: un último inbound
    enterrado detrás de muchos turnos largos del bot igual se encuentra."""
    c, vault = client
    filler = [
        {"role": "assistant", "content": "x" * 2_000, "timestamp": _iso(1_789_000_100_000)}
        for _ in range(200)  # ~400 KB después del inbound
    ]
    _seed_history(vault, "wa_573000000004", [
        {"role": "user", "content": "¿envían a Cali? \"sí\"", "timestamp": _iso(1_789_000_050_000)},
        *filler,
    ])

    assert _by_id(c)["wa_573000000004"]["last_inbound_ms"] == 1_789_000_050_000


def test_last_inbound_skips_corrupt_lines_and_missing_timestamps(client) -> None:
    c, vault = client
    hist = vault / "wa_573000000005" / "sessions" / "wa_573000000005.jsonl"
    hist.parent.mkdir(parents=True)
    hist.write_text(
        json.dumps({"role": "user", "content": "a", "timestamp": _iso(1_789_000_000_000)}) + "\n"
        + json.dumps({"role": "user", "content": "legacy sin timestamp"}) + "\n"
        + '{"role": "user", "content": "cort',  # escritura a medias
        encoding="utf-8",
    )

    assert _by_id(c)["wa_573000000005"]["last_inbound_ms"] == 1_789_000_000_000
