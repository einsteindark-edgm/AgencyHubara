"""Tests del use case `IngestInboundMessage` con fakes (no mocks).

Verifican:
* mensajes sin texto (media-only) se filtran sin tocar history ni routing.
* mensajes de texto se persisten en history y delegan al `LoadOrStartSalesSession`.
* el `session_id` se construye con prefijo `wa_` desde `from_number`.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.sales_whatsapp.use_cases.ingest_inbound_message import (
    IngestInboundMessage,
)
from src.sales_whatsapp.parsers import WhatsAppMessage


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
async def test_ignores_message_without_text():
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    use_case = IngestInboundMessage(history_store=history, load_session=loader)  # type: ignore[arg-type]

    await use_case.execute(_make_media_message())

    assert history.events == []
    assert loader.calls == []


@pytest.mark.asyncio
async def test_persists_user_event_and_delegates_to_load_session():
    history = FakeHistoryStore()
    loader = FakeLoadOrStart()
    use_case = IngestInboundMessage(history_store=history, load_session=loader)  # type: ignore[arg-type]

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
    )

    await use_case.execute(_make_text_message())

    assert order == ["history", "load_session"]
