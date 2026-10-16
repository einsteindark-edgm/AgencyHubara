"""`read_reply_window`: lo que el turno de ventas necesita saber mientras espera
a que vuelva el LLM (incidente 2026-10-15): si el chat sigue siendo del bot y
cuándo cierra la ventana de 24 h para el texto libre."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.plugins.chats.agent.sales.activities.reply_window import read_reply_window_activity
from src.plugins.chats.agent.sales.contracts import ReplyWindow

_SID = "wa_573001234567"
_CLOSES_AT_MS = 1_791_000_000_000


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("src.plugins.chats.agent.sales.activities.reply_window.WORKSPACE_VAULT_DIR", tmp_path)
    return tmp_path


def _write(vault: Path, data: dict) -> None:
    (vault / _SID).mkdir(parents=True, exist_ok=True)
    (vault / _SID / "metadata.json").write_text(json.dumps(data), encoding="utf-8")


async def test_it_reads_who_owns_the_chat_and_when_the_window_closes(vault: Path) -> None:
    _write(vault, {"active_route": "humano", "service_window_expires_at_ms": _CLOSES_AT_MS})

    assert await read_reply_window_activity(_SID) == ReplyWindow(route="humano", window_expires_at_ms=_CLOSES_AT_MS)


async def test_without_a_route_the_chat_is_the_bots(vault: Path) -> None:
    _write(vault, {"service_window_expires_at_ms": _CLOSES_AT_MS})

    assert (await read_reply_window_activity(_SID)).route == "ventas"


@pytest.mark.parametrize("raw", ["mañana", True, None])
async def test_an_expiry_that_is_not_a_time_is_unknown(vault: Path, raw: object) -> None:
    _write(vault, {"active_route": "ventas", "service_window_expires_at_ms": raw})

    assert (await read_reply_window_activity(_SID)).window_expires_at_ms is None


async def test_a_missing_or_damaged_document_reads_as_the_bots_with_no_known_window(vault: Path) -> None:
    assert await read_reply_window_activity(_SID) == ReplyWindow(route="ventas", window_expires_at_ms=None)

    (vault / _SID).mkdir(parents=True, exist_ok=True)
    (vault / _SID / "metadata.json").write_text("{dañado", encoding="utf-8")

    assert await read_reply_window_activity(_SID) == ReplyWindow(route="ventas", window_expires_at_ms=None)
