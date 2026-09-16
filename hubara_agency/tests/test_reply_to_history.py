"""Reply del cliente (cita de un mensaje) visible en el dashboard.

Caso real (sesión wa_573176471988, 2026-09-16): tras confirmar el pedido la
clienta escribió "Pero por favor, que el velón amor eterno sea este" CITANDO
un mensaje. El webhook traía `context.id`, pero el JSONL del chat solo
guardaba el texto — el operador no tenía forma de saber a qué foto se
refería. Estos tests exigen:

1. El ingest persiste en el evento del cliente su propio `wamid` y un
   `reply_to` (id citado + snapshot de la foto si la mandó el bot).
2. El endpoint del dashboard resuelve las citas a mensajes del propio JSONL
   (fotos/textos del cliente) para que la burbuja muestre qué se citó.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.platform.session_history import FilesystemMessageHistoryStore
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import (
    IngestInboundMessage,
)

_SESSION = "wa_5491111111111"
_PHOTO_URL = "https://assets.hubara.com.co/amor-eterno.webp"


class _RecordingHistoryStore:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def append_user_event(self, session_id: str, content: str, **kw: Any) -> None:
        self.events.append({"session_id": session_id, "content": content, **kw})


class _NoopLoadOrStart:
    async def execute(self, *a: Any, **k: Any) -> None:
        return None


class _MetadataStore:
    def __init__(self, seed: dict) -> None:
        self.store = dict(seed)

    def read(self, session_id: str) -> dict:
        return dict(self.store.get(session_id, {}))

    def write(self, session_id: str, data: dict) -> None:
        self.store[session_id] = dict(data)


def _use_case(history: _RecordingHistoryStore) -> IngestInboundMessage:
    seed = {
        _SESSION: {
            "outbound_media_index": {
                "wamid.bot.photo": {
                    "handle": "velon-amor-eterno",
                    "title": "Velón Amor Eterno",
                    "image_url": _PHOTO_URL,
                    "label": "Hero desktop",
                }
            }
        }
    }
    return IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=_NoopLoadOrStart(),  # type: ignore[arg-type]
        metadata_store=_MetadataStore(seed),  # type: ignore[arg-type]
    )


def _msg(message_id: str, quoted_id: str | None) -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id=message_id,
        from_number="5491111111111",
        phone_number_id="PID",
        text="que el velón amor eterno sea este",
        media=None,
        timestamp="1714312345",
        context={"from": "PID", "id": quoted_id} if quoted_id else None,
    )


@pytest.mark.asyncio
async def test_reply_to_bot_photo_persists_snapshot_and_wamid():
    history = _RecordingHistoryStore()
    await _use_case(history).execute(_msg("wamid.in.1", "wamid.bot.photo"))

    (event,) = history.events
    assert event["wamid"] == "wamid.in.1"
    assert event["reply_to"] == {
        "id": "wamid.bot.photo",
        "author": "agent",
        "text": "Velón Amor Eterno",
        "image_url": _PHOTO_URL,
    }


@pytest.mark.asyncio
async def test_reply_to_unknown_message_persists_only_the_id():
    """El id citado no está en el índice de fotos (texto del cliente, foto
    del cliente, mensaje evictado): se guarda el id — el dashboard lo
    resuelve contra el JSONL al leer."""
    history = _RecordingHistoryStore()
    await _use_case(history).execute(_msg("wamid.in.2", "wamid.client.photo"))

    (event,) = history.events
    assert event["reply_to"] == {"id": "wamid.client.photo"}


@pytest.mark.asyncio
async def test_message_without_reply_has_no_reply_to():
    history = _RecordingHistoryStore()
    await _use_case(history).execute(_msg("wamid.in.3", None))

    (event,) = history.events
    assert "reply_to" not in event
    assert event["wamid"] == "wamid.in.3"


@pytest.mark.asyncio
async def test_vision_reentry_persists_the_real_wamid():
    """El reentry de visión/transcripción usa ids sintéticos
    (`<wamid>_vision`); el cliente cita el wamid REAL de su foto, así que el
    evento debe guardar el wamid sin sufijo para poder resolverlo."""
    history = _RecordingHistoryStore()
    await _use_case(history).execute(_msg("wamid.img.7_vision", None))

    (event,) = history.events
    assert event["wamid"] == "wamid.img.7"


def test_store_persists_reply_to(tmp_path):
    store = FilesystemMessageHistoryStore(tmp_path)
    store.append_user_event(
        "wa_X", "esta", wamid="wamid.a", reply_to={"id": "wamid.b"}
    )
    import json

    line = (tmp_path / "wa_X" / "sessions" / "wa_X.jsonl").read_text().strip()
    event = json.loads(line)
    assert event["reply_to"] == {"id": "wamid.b"}
    assert event["wamid"] == "wamid.a"


@pytest.fixture
def client_with_temp_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "test_phone")
    with patch("src.plugins.chats.api.dashboard.WORKSPACE_VAULT_DIR", tmp_path):
        from src.main import app

        yield TestClient(app), tmp_path


def test_dashboard_resolves_reply_to_client_photo(client_with_temp_vault):
    client, vault = client_with_temp_vault
    store = FilesystemMessageHistoryStore(vault)
    store.append_user_event(
        "wa_R",
        "[el cliente envió una foto: vela de familia abrazada]",
        image_url="/api/dashboard/media/wa_R/1.jpg",
        wamid="wamid.client.photo",
    )
    store.append_user_event(
        "wa_R", "que sea este", wamid="wamid.in", reply_to={"id": "wamid.client.photo"}
    )

    msgs = client.get("/api/dashboard/sessions/wa_R").json()["messages"]

    assert msgs[1]["reply_to"] == {
        "id": "wamid.client.photo",
        "author": "user",
        "text": "[el cliente envió una foto: vela de familia abrazada]",
        "image_url": "/api/dashboard/media/wa_R/1.jpg",
    }


def test_dashboard_keeps_bot_photo_snapshot_and_unresolved_ids(
    client_with_temp_vault,
):
    client, vault = client_with_temp_vault
    store = FilesystemMessageHistoryStore(vault)
    snapshot = {
        "id": "wamid.bot.photo",
        "author": "agent",
        "text": "Velón Amor Eterno",
        "image_url": _PHOTO_URL,
    }
    store.append_user_event("wa_S", "esta", reply_to=snapshot)
    store.append_user_event("wa_S", "y esa", reply_to={"id": "wamid.gone"})

    msgs = client.get("/api/dashboard/sessions/wa_S").json()["messages"]

    assert msgs[0]["reply_to"] == snapshot
    assert msgs[1]["reply_to"] == {"id": "wamid.gone"}


# ── Premortem ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_string_quoted_id_is_ignored():
    """PM-04: un `context.id` no-string rompería el schema Zod del dashboard
    (parse de TODA la sesión). El ingest solo persiste ids string."""
    history = _RecordingHistoryStore()
    msg = replace(_msg("wamid.in.9", None), context={"id": 12345})
    await _use_case(history).execute(msg)

    (event,) = history.events
    assert "reply_to" not in event


def test_human_attachment_persists_wamid(tmp_path):
    """PM-03: el operador manda una foto/PDF y Meta devuelve su wamid; si no
    se persiste, la respuesta del cliente a ESA foto sale 'no disponible'."""
    store = FilesystemMessageHistoryStore(tmp_path)
    store.append_human_event(
        "wa_H", "mirá esta", image_url="/api/dashboard/media/wa_H/o.jpg", wamid="wamid.h1"
    )
    import json

    line = (tmp_path / "wa_H" / "sessions" / "wa_H.jsonl").read_text().strip()
    assert json.loads(line)["wamid"] == "wamid.h1"


def test_dashboard_resolves_reply_to_human_photo(client_with_temp_vault):
    client, vault = client_with_temp_vault
    store = FilesystemMessageHistoryStore(vault)
    store.append_human_event(
        "wa_HQ", "te muestro esta", image_url="/api/dashboard/media/wa_HQ/o.jpg", wamid="wamid.h1"
    )
    store.append_user_event("wa_HQ", "esa!", reply_to={"id": "wamid.h1"})

    msgs = client.get("/api/dashboard/sessions/wa_HQ").json()["messages"]

    assert msgs[1]["reply_to"] == {
        "id": "wamid.h1",
        "author": "human",
        "text": "te muestro esta",
        "image_url": "/api/dashboard/media/wa_HQ/o.jpg",
    }
