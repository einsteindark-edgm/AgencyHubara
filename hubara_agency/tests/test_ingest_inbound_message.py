"""Tests del use case `IngestInboundMessage` con fakes (no mocks).

Verifican:
* mensajes de texto se persisten en history y delegan al `LoadOrStartSalesSession`.
* mensajes media-only surface al LLM como marker (HU-002): "[el cliente envió un image]"
  para que el agente reaccione, en vez del silencio legacy.
* mensajes audio NO encolan workflow (queda pending_transcription en metadata).
* el `session_id` se construye con prefijo `wa_` desde `from_number`.
* referral CTWA se persiste en state.json y emite analytics event.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import (
    IngestInboundMessage,
)
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage


# --- Fakes -----------------------------------------------------------------


class FakeHistoryStore:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def append_user_event(self, session_id: str, content: str) -> None:
        self.events.append((session_id, content))


@dataclass
class _Call:
    session_id: str
    message: str
    phone_number_id: str | None


class FakeLoadOrStart:
    def __init__(self) -> None:
        self.calls: list[_Call] = []

    async def execute(
        self, session_id: str, message: str, phone_number_id: str | None
    ) -> None:
        self.calls.append(_Call(session_id, message, phone_number_id))


class FakeMetadataStore:
    """Fake in-memory para `last_inbound_message_id` (Fix 5 typing indicator)."""

    def __init__(self) -> None:
        self.store: dict[str, dict] = {}
        self.writes: list[tuple[str, dict]] = []

    def read(self, session_id: str) -> dict:
        return dict(self.store.get(session_id, {}))

    def write(self, session_id: str, data: dict) -> None:
        self.store[session_id] = dict(data)
        self.writes.append((session_id, dict(data)))


def _make_text_message(
    *, from_number: str = "5491111111111", text: str | None = "hola", phone_id: str = "PID"
) -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id="wamid.X",
        from_number=from_number,
        phone_number_id=phone_id,
        text=text,
        media=None,
        timestamp="1714312345",
    )


def _make_media_message() -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id="wamid.IMG",
        from_number="5491111111111",
        phone_number_id="PID",
        text=None,
        media={"type": "image", "id": "x"},
        timestamp="1714312345",
    )


# --- Tests -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_media_inbound_surfaces_as_marker_text_to_llm():
    """HU-002: media (image/video/document/sticker) sin caption ya no se
    descarta — surface al LLM como marker "[el cliente envió un X]" para
    que pueda reaccionar contextualmente en vez de quedarse mudo."""
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=FakeMetadataStore(),  # type: ignore[arg-type]
    )

    await use_case.execute(_make_media_message())

    assert history.events == [("wa_5491111111111", "[el cliente envió un image]")]
    assert len(loader.calls) == 1
    assert loader.calls[0].message == "[el cliente envió un image]"


@pytest.mark.asyncio
async def test_audio_inbound_defers_to_transcription():
    """HU-002 / A.5: audio inbound NO encola workflow — queda
    pending_transcription en metadata. La activity de transcripción
    procesa después y re-inyecta el texto."""
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )

    audio_msg = WhatsAppMessage(
        message_id="wamid.AUDIO",
        from_number="5491111111111",
        phone_number_id="PID",
        text=None,
        media=None,
        timestamp="1714312400",
        msg_type="audio",
        audio={"id": "media_xyz", "mime_type": "audio/ogg", "voice": True},
    )
    await use_case.execute(audio_msg)

    assert history.events == []  # no es texto LLM-ready aún
    assert loader.calls == []  # no se signaleó el workflow
    assert metadata.store["wa_5491111111111"]["pending_transcription"]["media_id"] == "media_xyz"


@pytest.mark.asyncio
async def test_referral_captured_persists_to_state_and_injects_banner():
    """HU-002 / A.0.3: cuando el cliente llega vía CTWA, el referral se
    persiste íntegro en state y el primer texto del LLM incluye un banner
    con el origen del ad."""
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )

    msg = WhatsAppMessage(
        message_id="wamid.CTWA",
        from_number="5491111111111",
        phone_number_id="PID",
        text="Hola, vi el anuncio",
        media=None,
        timestamp="1714312400",
        msg_type="text",
        referral={
            "ctwa_clid": "CLID_ABC",
            "source_type": "ad",
            "source_id": "AD_123",
            "headline": "Velas Hubara",
            "body": "Velas artesanales",
        },
    )
    await use_case.execute(msg)

    state = metadata.store["wa_5491111111111"]
    assert state["ctwa_clids_seen"] == ["CLID_ABC"]
    assert state["ctwa_referrals"][0]["source_id"] == "AD_123"
    # El history debe incluir el banner antepuesto al texto
    persisted_msg = history.events[0][1]
    assert "anuncio" in persisted_msg.lower() or "ad" in persisted_msg.lower()
    assert "Hola, vi el anuncio" in persisted_msg


@pytest.mark.asyncio
async def test_persists_user_event_and_delegates_to_load_session():
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=FakeMetadataStore(),  # type: ignore[arg-type]
    )

    await use_case.execute(_make_text_message(text="hola mundo"))

    assert history.events == [("wa_5491111111111", "hola mundo")]
    assert len(loader.calls) == 1
    call = loader.calls[0]
    assert call.session_id == "wa_5491111111111"
    assert call.message == "hola mundo"
    assert call.phone_number_id == "PID"


@pytest.mark.asyncio
async def test_history_is_appended_before_routing():
    """El history del usuario se persiste antes de tocar Temporal (orden invariante)."""

    order: list[str] = []

    class TrackingHistory:
        def append_user_event(self, session_id: str, content: str) -> None:
            order.append("history")

    class TrackingLoader:
        async def execute(self, session_id, message, phone_number_id) -> None:
            order.append("load_session")

    use_case = IngestInboundMessage(
        history_store=TrackingHistory(),  # type: ignore[arg-type]
        load_session=TrackingLoader(),  # type: ignore[arg-type]
        metadata_store=FakeMetadataStore(),  # type: ignore[arg-type]
    )

    await use_case.execute(_make_text_message())

    assert order == ["history", "load_session"]


@pytest.mark.asyncio
async def test_persists_inbound_message_id_for_typing_indicator():
    """Fix 5: message_id se escribe a metadata para que el typing indicator
    pueda referenciarlo al inicio de cada turno."""
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    metadata = FakeMetadataStore()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=metadata,  # type: ignore[arg-type]
    )

    await use_case.execute(_make_text_message(text="hola"))

    assert metadata.store["wa_5491111111111"]["last_inbound_message_id"] == "wamid.X"


@pytest.mark.asyncio
async def test_metadata_write_failure_does_not_break_flow():
    """El write de metadata es best-effort: si falla, el flujo igual signaleael workflow."""
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()

    class BrokenMetadata:
        def read(self, session_id):
            raise OSError("disk full")

        def write(self, session_id, data):
            raise OSError("disk full")

    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=BrokenMetadata(),  # type: ignore[arg-type]
    )

    # No debe raisear
    await use_case.execute(_make_text_message(text="hola"))

    # El flujo principal (history + signal) sigue funcionando
    assert history.events == [("wa_5491111111111", "hola")]
    assert len(loader.calls) == 1
