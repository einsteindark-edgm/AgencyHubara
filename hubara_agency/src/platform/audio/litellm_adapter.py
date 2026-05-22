"""Adapter de transcripción vía litellm — alineado con el resto del proyecto.

Por qué litellm:
  * El proyecto YA usa litellm como proxy unificado (`API_BASE_LLMLITE` en
    `src/platform/config.py`, consumido en `registries.build_default_llm_config`).
    El motor de conversación (`exoclaw_temporal.activities.llm.llm_chat`)
    ya enruta a través de litellm.
  * Cambiar el modelo de STT (ej. `gemini/gemini-2.5-flash-lite` → `gemini/gemini-3-flash-lite`
    cuando salga) es un toggle de env var, sin tocar código ni redeploy.
  * litellm soporta Gemini multimodal audio nativo desde 2025 — el modelo
    recibe el audio en base64 dentro del mensaje y devuelve texto.
  * Más adelante, cuando habilitemos TTS outbound (no en esta HU), se puede
    agregar `litellm_tts_adapter.py` simétrico que apunta al MISMO proxy.

Patrón litellm para audio input multimodal:

    response = await litellm.acompletion(
        model="gemini/gemini-2.5-flash-lite",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "..."},
                {"type": "file", "file": {"file_data": "data:audio/ogg;base64,<b64>"}}
            ]
        }],
    )

Modelo por default: `gemini/gemini-2.5-flash-lite`. Configurable via env
`AUDIO_TRANSCRIPTION_MODEL`. Cuando salga 3.0 / 3.1, basta cambiar el env.

DEHA:
  * R-JSON: input/output JSON-safe (request/result dataclasses).
  * R-STATELESS: sin module-level cache; el client litellm es global pero
    no contiene state mutable de sesiones.
  * Sin imports de Temporal — adapter puro.
"""
from __future__ import annotations

import base64
import os
import time

import litellm
import structlog

from src.platform.audio.dtos import TranscriptionRequest, TranscriptionResult
from src.platform.audio.meta_media_fetcher import fetch_media_bytes
from src.platform.config import API_BASE_LLMLITE

logger = structlog.get_logger()

# Costo estimado por segundo de audio para Gemini Flash-Lite.
# Cálculo: ~25 tokens/segundo input @ $0.10/1M tokens audio + ~10 tokens output
# por segundo @ $0.40/1M output. Total ≈ $0.000003/segundo audio.
# Si cambiás el modelo, este número solo afecta la métrica de cost_usd_estimate,
# no la lógica.
_GEMINI_FLASH_LITE_COST_PER_SECOND = 0.000004


_PROMPT_ES = (
    "Transcribe EXACTAMENTE el contenido del audio en español. "
    "NO traduzcas. NO resumas. NO agregues comentarios. "
    "Devolvé SOLO el texto transcrito, sin prefijos ni metadata. "
    "Si el audio está vacío o es ininteligible, devolvé exactamente "
    "el texto: [INAUDIBLE]"
)

# PREMORTEM #6: defensive lookup de RateLimitError (puede no existir
# en versiones antiguas de litellm). Si no está, atrapamos por Exception
# y mapeamos en el except genérico.
_LITELLM_RATE_LIMIT_ERR = getattr(litellm, "RateLimitError", None)

# PREMORTEM #7: variantes que tratamos como "audio ininteligible".
# Gemini puede no respetar la instrucción "devolvé exactamente [INAUDIBLE]"
# y devolver variantes en mayúsculas/minúsculas o frases naturales.
_INAUDIBLE_MARKERS = (
    "[inaudible]",
    "(inaudible)",
    "inaudible",
    "no se entiende",
    "no se escucha",
    "audio vacío",
    "audio vacio",
    "no hay audio",
    "sin audio",
)
# Mínimo de chars para considerar transcripción "útil". Un audio que
# transcribe a "sí" o "ok" es útil, pero "..." o "a" no.
_MIN_USEFUL_CHARS = 2


class LiteLLMTranscriptionAdapter:
    """Transcripción vía litellm + Gemini Flash-Lite (default) u otro modelo
    multimodal con audio input que litellm soporte."""

    def __init__(
        self,
        model: str = "gemini/gemini-2.5-flash-lite",
        api_base: str | None = None,
        api_key: str | None = None,
        cost_per_second_usd: float = _GEMINI_FLASH_LITE_COST_PER_SECOND,
    ) -> None:
        self._model = model
        self._api_base = api_base
        self._api_key = api_key
        self._cost_per_second = cost_per_second_usd
        # Identificador opaco para logs / analytics
        self.name = model.replace("/", "_")

    @classmethod
    def from_env(cls) -> "LiteLLMTranscriptionAdapter":
        """Construye desde env vars.

        Variables:
          * `AUDIO_TRANSCRIPTION_MODEL` (default: `gemini/gemini-2.5-flash-lite`)
          * `AUDIO_TRANSCRIPTION_API_BASE` (default: `API_BASE_LLMLITE` del
            proyecto — el proxy litellm ya configurado)
          * `AUDIO_TRANSCRIPTION_API_KEY` (opcional; si el proxy maneja auth,
            no hace falta. Si llamás directo a Gemini sin proxy, usa
            `GEMINI_API_KEY`)
        """
        model = (
            os.getenv("AUDIO_TRANSCRIPTION_MODEL") or "gemini/gemini-2.5-flash-lite"
        )
        # api_base: si el proxy litellm del proyecto está disponible (en local
        # development corre en localhost:4000), lo usamos. Si querés bypasear
        # el proxy y pegarle directo a Gemini, dejá `AUDIO_TRANSCRIPTION_API_BASE=""`
        api_base = (
            os.getenv("AUDIO_TRANSCRIPTION_API_BASE")
            if os.getenv("AUDIO_TRANSCRIPTION_API_BASE") is not None
            else API_BASE_LLMLITE
        )
        api_key = (
            os.getenv("AUDIO_TRANSCRIPTION_API_KEY")
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("LITELLM_API_KEY")
        )
        return cls(model=model, api_base=api_base or None, api_key=api_key or None)

    async def transcribe(
        self, request: TranscriptionRequest
    ) -> TranscriptionResult:
        # 1. Fetch media bytes de Meta (URL temporal, expira en 5 min)
        fetched = await fetch_media_bytes(request.media_id)
        if fetched is None:
            return TranscriptionResult(
                text="",
                ok=False,
                error="media_fetch_failed",
                provider=self.name,
            )
        audio_bytes, mime_type = fetched
        # Normaliza mime — Gemini acepta audio/mp3, audio/mp4, audio/ogg.
        # WhatsApp manda voice notes en audio/ogg; otros audios en audio/mp4 o
        # audio/amr. Mapeamos amr → ogg (Gemini lo procesa).
        mime_for_llm = _normalize_mime(mime_type or "audio/ogg")
        b64 = base64.b64encode(audio_bytes).decode("ascii")

        # 2. Llamar a litellm
        started = time.time()
        try:
            response = await litellm.acompletion(
                model=self._model,
                api_base=self._api_base,
                api_key=self._api_key,
                temperature=0,  # determinístico — no queremos "limpieza creativa"
                max_tokens=2048,  # 1 min audio = ~150 palabras; cap de seguridad
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": _PROMPT_ES},
                            {
                                "type": "file",
                                "file": {
                                    "file_data": f"data:{mime_for_llm};base64,{b64}",
                                },
                            },
                        ],
                    }
                ],
            )
        except Exception as e:  # noqa: BLE001 — transport / 5xx / rate / etc.
            # PREMORTEM #6: defensive — RateLimitError puede no existir
            # según version. Atrapamos amplio y categorizamos.
            err_type = type(e).__name__
            is_rate_limit = (
                _LITELLM_RATE_LIMIT_ERR is not None
                and isinstance(e, _LITELLM_RATE_LIMIT_ERR)
            ) or "RateLimit" in err_type
            error_code = "rate_limit" if is_rate_limit else f"provider_error: {err_type}"
            if not is_rate_limit:
                logger.warning(
                    "litellm_transcribe.error",
                    model=self._model,
                    error=str(e),
                    error_type=err_type,
                )
            return TranscriptionResult(
                text="",
                ok=False,
                error=error_code,
                provider=self.name,
                latency_ms=int((time.time() - started) * 1000),
            )

        latency_ms = int((time.time() - started) * 1000)

        # 3. Extraer texto
        try:
            text = (response.choices[0].message.content or "").strip()
        except (AttributeError, IndexError, KeyError) as e:
            logger.warning(
                "litellm_transcribe.bad_response_shape",
                model=self._model,
                error=str(e),
            )
            return TranscriptionResult(
                text="",
                ok=False,
                error="bad_response_shape",
                provider=self.name,
                latency_ms=latency_ms,
            )

        # PREMORTEM #7: chequeo relaxed contra variantes de "inaudible".
        # Gemini puede devolver "[Inaudible]", "(inaudible)", "no se entiende",
        # etc. — no respeta siempre la instrucción "devolvé exactamente
        # [INAUDIBLE]". Hacemos match case-insensitive + cap mínimo.
        text_lower = text.lower().strip()
        is_inaudible = any(marker in text_lower for marker in _INAUDIBLE_MARKERS)
        if not text or is_inaudible:
            return TranscriptionResult(
                text="",
                ok=False,
                error="empty" if not text else "inaudible",
                provider=self.name,
                latency_ms=latency_ms,
            )
        # Cap mínimo — audios que transcriben a 1 char son ruido.
        if len(text) < _MIN_USEFUL_CHARS:
            return TranscriptionResult(
                text="",
                ok=False,
                error="too_short",
                provider=self.name,
                latency_ms=latency_ms,
            )

        # 4. Calcular duración + costo aproximados
        # Gemini no devuelve `duration` como Whisper. Estimamos por tokens
        # input (25 tok/s para audio). Si litellm expone el usage:
        duration_seconds: float | None = None
        cost_estimate: float | None = None
        try:
            usage = response.usage  # type: ignore[attr-defined]
            prompt_tokens = getattr(usage, "prompt_tokens", None)
            if prompt_tokens:
                # Gemini factura audio a 25 tok/segundo en input
                duration_seconds = max(0.0, (prompt_tokens - 50) / 25.0)
                cost_estimate = duration_seconds * self._cost_per_second
        except Exception:  # noqa: BLE001
            pass

        # 5. Aplicar regla de duración máxima (Hubara A.5: >60s → too_long)
        if duration_seconds and duration_seconds > request.max_duration_seconds:
            return TranscriptionResult(
                text=text,
                ok=False,
                error="too_long",
                provider=self.name,
                duration_seconds=duration_seconds,
                cost_usd_estimate=cost_estimate,
                latency_ms=latency_ms,
            )

        return TranscriptionResult(
            text=text,
            ok=True,
            duration_seconds=duration_seconds,
            provider=self.name,
            cost_usd_estimate=cost_estimate,
            latency_ms=latency_ms,
        )


def _normalize_mime(mime: str) -> str:
    """Normaliza mime type WA → mime aceptado por Gemini.

    Gemini acepta: audio/mp3, audio/mp4, audio/ogg, audio/wav, audio/flac,
    audio/aac, audio/aiff.

    WhatsApp manda audios en:
      * audio/ogg; codecs=opus  (voice notes)
      * audio/mp4              (audios desde galería)
      * audio/amr              (raro pero ocurre)
      * audio/aac
    """
    mime = mime.lower().split(";")[0].strip()
    if mime in {"audio/ogg", "audio/opus"}:
        return "audio/ogg"
    if mime in {"audio/mp4", "audio/aac"}:
        return "audio/mp4"
    if mime == "audio/amr":
        # Gemini no soporta AMR directo. Devolvemos ogg para que al menos
        # intente — algunos backends lo aceptan transparente.
        return "audio/ogg"
    if mime.startswith("audio/"):
        return mime
    # Fallback defensivo
    return "audio/ogg"
