"""DTOs JSON-safe para el pipeline de transcripción.

R-JSON: frozen dataclasses, sin tipos no-serializables (no datetime, no
Path; usamos epoch ms + str). Diseñados para cruzar workflow boundary.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TranscriptionRequest:
    """Pedido de transcripción.

    Atributos:
      media_id: id del media en Meta (a descargar via Graph API).
      mime_type: mime declarado por Meta (audio/ogg, audio/mp4, audio/amr,
        audio/aac, audio/mpeg). Whisper acepta todos.
      voice_note: True si es nota de voz nativa de WA (PTT). Útil para
        log y para decidir si bajar quality threshold.
      language_hint: ISO-639 ("es" por default). Whisper auto-detecta pero
        el hint mejora accuracy.
      max_duration_seconds: cap defensivo. Audios > este límite se
        rechazan con `error="too_long"` ANTES de gastar API call. La regla
        Hubara dice >60s → escalar a humano.
    """

    media_id: str
    mime_type: str
    voice_note: bool = False
    language_hint: str = "es"
    max_duration_seconds: int = 60


@dataclass(frozen=True)
class TranscriptionResult:
    """Resultado de la transcripción.

    Atributos:
      text: texto transcrito en español. Vacío si error o silencio.
      ok: True si la transcripción fue exitosa.
      error: detalle si ok=False ("provider_error", "too_long", "empty",
        "media_fetch_failed", "rate_limit").
      duration_seconds: duración detectada del audio (post-fetch).
      provider: cuál adapter respondió ("groq" | "openai" | "deepgram" | "fake").
      cost_usd_estimate: costo estimado para attribution.
      latency_ms: latencia del provider call.
    """

    text: str
    ok: bool
    error: str | None = None
    duration_seconds: float | None = None
    provider: str | None = None
    cost_usd_estimate: float | None = None
    latency_ms: int | None = None
