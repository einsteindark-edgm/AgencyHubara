"""Puerto de transcripción de audio (Protocol — DEHA-friendly).

Implementations:

* `GroqWhisperAdapter`
* `OpenAIWhisperAdapter`
* `FallbackTranscriptionAdapter` (compone N en orden)
* `FakeTranscriptionAdapter` (para tests)
"""
from __future__ import annotations

from typing import Protocol

from src.platform.audio.dtos import TranscriptionRequest, TranscriptionResult


class AudioTranscriptionPort(Protocol):
    """Transcribe audio inbound de WhatsApp a texto."""

    name: str

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        """Descarga el media de Meta y lo transcribe.

        Implementations deben:
          1. Si `media_id` ya fue resuelto a bytes (cache externo), usar.
             Si no, llamar a `meta_media_fetcher.fetch_media_bytes`.
          2. Validar duración ≤ `max_duration_seconds` ANTES del API call.
          3. Llamar al provider STT.
          4. Devolver `TranscriptionResult` con text + ok.

        NO debe raisear excepciones — todo error va en `result.error`. La
        activity Temporal de arriba decide si reintenta basándose en
        `result.error` (rate_limit → retry, provider_error → retry,
        empty → no retry, too_long → no retry).
        """
        ...
