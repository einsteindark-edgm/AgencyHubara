"""Composition root del transcription layer.

Default: **Gemini Flash-Lite via litellm**. Cambiar el modelo es un toggle
de env (`AUDIO_TRANSCRIPTION_MODEL=gemini/gemini-3-flash-lite` cuando salga).

El proyecto ya usa litellm como proxy unificado (`API_BASE_LLMLITE` en
`src/platform/config.py`) — esta capa de transcripción enchufa al MISMO
proxy. Consistencia con el resto del agente.

Modos:

  AUDIO_TRANSCRIPTION_PROVIDER=auto      # default — usa litellm (Gemini Flash-Lite)
  AUDIO_TRANSCRIPTION_PROVIDER=litellm   # idem
  AUDIO_TRANSCRIPTION_PROVIDER=fake      # adapter de tests, devuelve texto sintético

Modelo configurable:
  AUDIO_TRANSCRIPTION_MODEL=gemini/gemini-2.5-flash-lite  # default
  AUDIO_TRANSCRIPTION_MODEL=gemini/gemini-2.5-flash       # más caro pero más capaz
  AUDIO_TRANSCRIPTION_MODEL=openai/whisper-1              # fallback si Gemini falla mucho

API base override (opcional — por default usa el proxy del proyecto):
  AUDIO_TRANSCRIPTION_API_BASE=""                         # vacío = bypass proxy, llamada directa
  AUDIO_TRANSCRIPTION_API_BASE=http://litellm-proxy:4000  # proxy custom

API key (solo si NO usás el proxy y llamás directo a Gemini):
  GEMINI_API_KEY=... o AUDIO_TRANSCRIPTION_API_KEY=...

Decisión de arquitectura (mayo 2026): se eliminaron los adapters directos a
Groq y OpenAI porque (a) el operador no confía en Groq, (b) Groq TTS aún no
soporta español, y necesitamos consistencia para cuando se habilite audio
bidireccional, (c) el proyecto ya estandarizó litellm como proxy unificado.
"""
from __future__ import annotations

import os
from functools import lru_cache

import structlog

from src.platform.audio.dtos import TranscriptionRequest, TranscriptionResult
from src.platform.audio.litellm_adapter import LiteLLMTranscriptionAdapter
from src.platform.audio.port import AudioTranscriptionPort

logger = structlog.get_logger()


class _NullTranscriptionAdapter:
    """Adapter no-op cuando no hay provider configurado.

    Devuelve `error="no_provider_configured"`. El caller debe avisar al
    cliente que escriba en lugar del audio.
    """

    name = "null"

    async def transcribe(
        self, request: TranscriptionRequest
    ) -> TranscriptionResult:
        return TranscriptionResult(
            text="",
            ok=False,
            error="no_provider_configured",
            provider=self.name,
        )


class _FakeTranscriptionAdapter:
    """Adapter para tests / dev sin conexión a Gemini. Devuelve texto
    sintético basado en `media_id`. Se activa con
    `AUDIO_TRANSCRIPTION_PROVIDER=fake`.
    """

    name = "fake"

    async def transcribe(
        self, request: TranscriptionRequest
    ) -> TranscriptionResult:
        return TranscriptionResult(
            text=f"[FAKE TRANSCRIBE de media={request.media_id}]",
            ok=True,
            duration_seconds=3.0,
            provider=self.name,
            cost_usd_estimate=0.0,
            latency_ms=10,
        )


@lru_cache(maxsize=1)
def get_audio_transcription_port() -> AudioTranscriptionPort:
    """Devuelve el port singleton según env.

    Default: `LiteLLMTranscriptionAdapter` apuntando a `gemini/gemini-2.5-flash-lite`
    via el proxy `API_BASE_LLMLITE` ya configurado en el proyecto.
    """
    provider = (os.getenv("AUDIO_TRANSCRIPTION_PROVIDER") or "auto").lower()

    if provider == "fake":
        logger.info("audio.composition.using_fake_adapter")
        return _FakeTranscriptionAdapter()

    if provider in {"auto", "litellm"}:
        adapter = LiteLLMTranscriptionAdapter.from_env()
        logger.info(
            "audio.composition.litellm_configured",
            model=adapter._model,
            api_base=adapter._api_base or "(litellm default)",
        )
        return adapter

    # provider explícito desconocido — fallback seguro
    logger.warning(
        "audio.composition.unknown_provider",
        provider=provider,
        falling_back_to="null",
    )
    return _NullTranscriptionAdapter()
