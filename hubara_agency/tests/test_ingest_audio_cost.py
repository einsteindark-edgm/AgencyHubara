"""Lo que cuesta transcribir una nota de voz queda en la conversación.

Como el costo del LLM, el de WhatsApp, el de Jev y el de leer las fotos:
`episodes[].audio_usage = {calls, cost_usd_micros}`. Se cobra toda llamada que
Google contestó (también si devolvió vacío, "inaudible" o el audio era largo);
no se cobra la que nunca llegó al modelo (no se pudo bajar el audio de Meta, o
el proveedor falló).
"""
from __future__ import annotations

from typing import Any

import pytest

from src.platform.audio.dtos import TranscriptionResult
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from tests.test_ingest_image_vision import (
    FakeHistoryStore,
    FakeLoadOrStart,
    FakeMetadataStore,
    _make_use_case,
)

SID = "wa_5491111111111"


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


class _Port:
    def __init__(self, result: TranscriptionResult) -> None:
        self.result = result

    async def transcribe(self, request: Any) -> TranscriptionResult:
        return self.result


async def _transcribe(monkeypatch, result: TranscriptionResult) -> FakeMetadataStore:
    from src.platform.audio import composition
    from src.platform.whatsapp import client as wa_client

    async def _no_send(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(composition, "get_audio_transcription_port", lambda: _Port(result))
    monkeypatch.setattr(wa_client, "send_message", _no_send)
    metadata = FakeMetadataStore(seed={"episodes": [{"episode_id": "ep_001", "closed_at_ms": None}]})
    use_case = _make_use_case(FakeHistoryStore(), FakeLoadOrStart(), metadata)

    await use_case._transcribe_and_reenter(_voice_note())
    return metadata


def _audio_usage(metadata: FakeMetadataStore) -> Any:
    episodes = metadata.store[SID].get("episodes") or []
    return episodes[0].get("audio_usage") if episodes else None


@pytest.mark.asyncio
async def test_a_transcribed_voice_note_is_charged_to_the_conversation(monkeypatch) -> None:
    metadata = await _transcribe(
        monkeypatch,
        TranscriptionResult(text="quiero una vela de lavanda", ok=True, cost_usd_estimate=0.000256, latency_ms=900),
    )

    assert _audio_usage(metadata) == {"calls": 1, "cost_usd_micros": 256}


@pytest.mark.asyncio
async def test_a_voice_note_the_model_could_not_understand_is_charged_too(monkeypatch) -> None:
    metadata = await _transcribe(
        monkeypatch,
        TranscriptionResult(text="", ok=False, error="inaudible", cost_usd_estimate=0.0002, latency_ms=800),
    )

    assert _audio_usage(metadata) == {"calls": 1, "cost_usd_micros": 200}


@pytest.mark.asyncio
@pytest.mark.parametrize("error", ["media_fetch_failed", "rate_limit", "provider_error: Timeout"])
async def test_a_voice_note_that_never_reached_the_model_is_not_charged(monkeypatch, error) -> None:
    metadata = await _transcribe(monkeypatch, TranscriptionResult(text="", ok=False, error=error))

    assert _audio_usage(metadata) is None
