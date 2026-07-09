"""Reply del cliente citando una foto → nota determinista al LLM.

Caso real (sesión wa_573125671604): el bot mandó 4 fotos del Duo Zodiacal;
el cliente respondió "Esta me gusta" CITANDO una foto específica. El webhook
trae `context.id` (wamid del mensaje citado) y el flush ya persiste
`outbound_media_index[wamid] = {handle, title, image_url, label}` — este
test exige que el ingest los cruce y le diga al LLM QUÉ foto citó el
cliente vía `extra_context` (mismo patrón que la nota del order_draft).
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import (
    IngestInboundMessage,
)


class FakeHistoryStore:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def append_user_event(
        self, session_id: str, content: str, *, image_url: str | None = None
    ) -> None:
        self.events.append((session_id, content))


@dataclass
class _Call:
    session_id: str
    message: str
    phone_number_id: str | None
    extra_context: list[str] | None = None


class FakeLoadOrStart:
    def __init__(self) -> None:
        self.calls: list[_Call] = []

    async def execute(
        self,
        session_id: str,
        message: str,
        phone_number_id: str | None,
        extra_context: list[str] | None = None,
    ) -> None:
        self.calls.append(
            _Call(session_id, message, phone_number_id, extra_context)
        )


class FakeMetadataStore:
    def __init__(self, seed: dict | None = None) -> None:
        self.store: dict[str, dict] = dict(seed or {})

    def read(self, session_id: str) -> dict:
        return dict(self.store.get(session_id, {}))

    def write(self, session_id: str, data: dict) -> None:
        self.store[session_id] = dict(data)


_SESSION = "wa_5491111111111"
_LEO_URL = "https://assets.hubara.com.co/leo-01KW2SQSD4RP0KSM9HTJ38QPEF.webp"


def _metadata_with_index() -> dict:
    return {
        _SESSION: {
            "outbound_media_index": {
                "wamid.img.3": {
                    "handle": "duo-zodiacal",
                    "title": "Duo Zodiacal",
                    "image_url": _LEO_URL,
                    "label": "Leo",
                },
            },
        }
    }


def _reply_message(quoted_id: str | None) -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id="wamid.reply",
        from_number="5491111111111",
        phone_number_id="PID",
        text="Esta me gusta",
        media=None,
        timestamp="1714312345",
        context={"from": "PID", "id": quoted_id} if quoted_id else None,
    )


@pytest.mark.asyncio
async def test_reply_to_known_photo_injects_citation_note():
    loader = FakeLoadOrStart()
    use_case = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=FakeMetadataStore(_metadata_with_index()),  # type: ignore[arg-type]
    )

    await use_case.execute(_reply_message("wamid.img.3"))

    (call,) = loader.calls
    assert call.message == "Esta me gusta"
    note = "\n".join(call.extra_context or [])
    # La nota nombra producto y diseño — "esa" deja de ser ambiguo
    assert "Duo Zodiacal" in note
    assert "Leo" in note
    assert "duo-zodiacal" in note


@pytest.mark.asyncio
async def test_reply_to_unknown_wamid_injects_no_note():
    """Cita de un mensaje que no está en el índice (texto, mensaje viejo
    evictado) → sin nota; el LLM sigue con el texto solo."""
    loader = FakeLoadOrStart()
    use_case = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=FakeMetadataStore(_metadata_with_index()),  # type: ignore[arg-type]
    )

    await use_case.execute(_reply_message("wamid.txt.99"))

    (call,) = loader.calls
    assert call.extra_context is None


@pytest.mark.asyncio
async def test_message_without_context_injects_no_note():
    loader = FakeLoadOrStart()
    use_case = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=FakeMetadataStore(_metadata_with_index()),  # type: ignore[arg-type]
    )

    await use_case.execute(_reply_message(None))

    (call,) = loader.calls
    assert call.extra_context is None
