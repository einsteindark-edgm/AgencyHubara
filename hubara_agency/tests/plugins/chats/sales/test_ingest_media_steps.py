"""La visión y el audio como pasos del ingest (entrada durable, fase 2, paso 2).

Hasta la fase 1 la foto y la nota de voz se leían en una tarea suelta
(`_spawn_safe`): la activity del ingest volvía apenas la lanzaba, y si el
worker moría a mitad de la lectura la foto se perdía. Ahora cada una es un paso
(`describe_image`, `transcribe_audio`) que devuelve la reentrada del mensaje
como texto: el workflow la corre como otro mensaje, con su historial y su
entrega, y en este proceso `execute` vuelve con la foto ya en el bot.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pytest
from temporalio.converter import DataConverter

from src.platform.audio.dtos import TranscriptionResult
from src.plugins.chats.agent.sales.contracts import IngestRequest
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage
from tests.test_ingest_image_vision import FakeHistoryStore, FakeLoadOrStart, FakeMetadataStore

_ARRIVED_MS = 1_714_312_346_000


def _photo(media_id: str = "product_1", *, caption: str | None = None) -> WhatsAppMessage:
    media: dict[str, Any] = {"type": "image", "id": media_id, "mime_type": "image/jpeg"}
    if caption is not None:
        media["caption"] = caption
    return WhatsAppMessage(
        message_id="wamid.IMG",
        from_number="5491111111111",
        phone_number_id="PID",
        text=None,
        media=media,
        timestamp="1714312345",
        msg_type="image",
    )


def _voice_note() -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id="wamid.AUDIO",
        from_number="5491111111111",
        phone_number_id="PID",
        text=None,
        media=None,
        audio={"id": "aud_1", "mime_type": "audio/ogg", "voice": True},
        timestamp="1714312345",
        msg_type="audio",
    )


class _Notices:
    def __init__(self) -> None:
        self.seen: list[tuple[str, bool]] = []

    async def __call__(self, session_id: str, wamid: str, done: bool) -> None:
        self.seen.append((wamid, done))


def _ingest(**kwargs: Any) -> tuple[IngestInboundMessage, FakeHistoryStore, FakeLoadOrStart]:
    history, loader = FakeHistoryStore(), FakeLoadOrStart()
    use_case = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=FakeMetadataStore(),  # type: ignore[arg-type]
        **kwargs,
    )
    return use_case, history, loader


async def _across_temporal(value: Any) -> IngestRequest:
    converter = DataConverter.default
    [decoded] = await converter.decode(await converter.encode([value]), [IngestRequest])
    return decoded


@pytest.fixture
def fake_vision(monkeypatch: pytest.MonkeyPatch):
    from src.platform.vision.composition import get_image_vision_port

    monkeypatch.setenv("IMAGE_VISION_PROVIDER", "fake")
    get_image_vision_port.cache_clear()
    yield
    get_image_vision_port.cache_clear()


@pytest.fixture
def transcription(monkeypatch: pytest.MonkeyPatch):
    """Transcripción falsa (sin red) y envíos al cliente registrados."""
    from src.platform.audio import composition
    from src.platform.whatsapp import client as wa_client

    sent: list[str] = []
    state: dict[str, TranscriptionResult] = {"result": TranscriptionResult(text="quiero una vela de lavanda", ok=True)}

    class _Port:
        async def transcribe(self, request: Any) -> TranscriptionResult:
            return state["result"]

    async def _send(_pid: str, _to: str, text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(composition, "get_audio_transcription_port", lambda: _Port())
    monkeypatch.setattr(wa_client, "send_message", _send)
    return state, sent


@pytest.mark.asyncio
async def test_a_photo_is_in_the_bot_when_execute_returns(fake_vision) -> None:
    use_case, history, loader = _ingest()

    await use_case.execute(_photo())

    assert [call.message for call in loader.calls] and loader.calls[0].message.startswith("[el cliente envió una foto")
    assert len(history.events) == 1  # la foto entró una vez al chat


@pytest.mark.asyncio
async def test_the_photo_step_hands_back_its_reentry_across_temporal(fake_vision) -> None:
    use_case, _history, loader = _ingest()

    reentry = await _across_temporal(
        await use_case.describe_image(IngestRequest(message=asdict(_photo(caption="¿tienes esta?")), received_at_ms=_ARRIVED_MS))
    )

    assert reentry.message["message_id"] == "wamid.IMG_vision"
    assert reentry.from_photo is True
    assert reentry.received_at_ms == _ARRIVED_MS  # el reloj sigue siendo la llegada de la foto
    assert (reentry.customer_text, reentry.customer_text_from_message) == ("¿tienes esta?", False)
    assert loader.calls == []  # el paso de la visión no le habla a la sesión

    await use_case.run(reentry)

    assert loader.calls[0].message.startswith("[el cliente envió una foto")


@pytest.mark.asyncio
async def test_a_voice_note_is_in_the_bot_when_execute_returns(transcription) -> None:
    use_case, history, loader = _ingest()

    await use_case.execute(_voice_note())

    assert [call.message for call in loader.calls] == ["quiero una vela de lavanda"]
    assert history.events[0][1] == "quiero una vela de lavanda"


@pytest.mark.asyncio
async def test_the_audio_step_hands_back_its_reentry_or_nothing(transcription) -> None:
    state, sent = transcription
    use_case, _history, loader = _ingest()

    reentry = await _across_temporal(
        await use_case.transcribe_audio(IngestRequest(message=asdict(_voice_note()), received_at_ms=_ARRIVED_MS))
    )
    assert reentry.message["message_id"] == "wamid.AUDIO_transcribed"
    assert reentry.message["text"] == "quiero una vela de lavanda"
    assert reentry.received_at_ms == _ARRIVED_MS

    state["result"] = TranscriptionResult(text=None, ok=False, error="inaudible")
    assert await use_case.transcribe_audio(IngestRequest(message=asdict(_voice_note()))) is None
    assert len(sent) == 1  # «no logré entenderlo»: se le pide que lo escriba
    assert loader.calls == []


@pytest.mark.asyncio
async def test_the_sales_session_hears_the_photo_is_done_even_if_reading_it_breaks(monkeypatch, fake_vision) -> None:
    notices = _Notices()
    use_case, _history, _loader = _ingest(photo_notifier=notices)

    async def _broken(_request: Any) -> Any:
        raise RuntimeError("visión rota")

    monkeypatch.setattr(use_case, "describe_image", _broken)
    with pytest.raises(RuntimeError):
        await use_case.execute(_photo())

    assert notices.seen == [("wamid.IMG", False), ("wamid.IMG", True)]
