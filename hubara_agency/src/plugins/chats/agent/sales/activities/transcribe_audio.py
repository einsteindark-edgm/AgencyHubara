"""Activity: transcribe audio inbound y reentry al ingest con texto sintético.

Pipeline:

  1. Lee `metadata.json[pending_transcription]` (media_id + message_id).
  2. Llama `AudioTranscriptionPort.transcribe()` (Groq → OpenAI fallback).
  3. Si OK: signal al workflow del agente con `send_message` que tenga el
     texto transcrito (rol "user" sintético). Si NO hay workflow corriendo
     (caso primer audio sin sesión activa), reentry vía `IngestInboundMessage`
     con un `WhatsAppMessage` sintético tipo `text`.
  4. Si falla: persiste error en metadata + dispatch un texto al cliente
     pidiendo que lo escriba.

DEHA:
  * R-HEARTBEAT: heartbeat cada 5s — fetch+transcribe puede tardar varios s.
  * R-JSON: in/out son strings (session_id).
  * R-STATELESS: el port se obtiene via `get_audio_transcription_port()`
    (lru_cache singleton).
"""
from __future__ import annotations

import json
import os
from typing import Any

from temporalio import activity

from src.platform.constants import WHATSAPP_SESSION_PREFIX
from src.platform.temporal.heartbeat import with_heartbeat


@activity.defn(name="transcribe_audio_activity")
@with_heartbeat(every=5)
async def transcribe_audio_activity(session_id: str) -> str:
    """Transcribe el audio pending para esta sesión y lo signal-ea al workflow.

    Devuelve el texto transcrito (vacío si falló). Diseñada para correr
    inmediatamente después de que el ingest detecta audio inbound.
    """
    from src.platform.analytics import (
        get_event_bus,
        make_wa_interaction,
    )
    from src.platform.audio.composition import get_audio_transcription_port
    from src.platform.audio.dtos import TranscriptionRequest
    from src.platform.config import WORKSPACE_VAULT_DIR
    from src.platform.whatsapp import client as wa_client

    metadata_file = WORKSPACE_VAULT_DIR / session_id / "metadata.json"
    if not metadata_file.exists():
        return ""

    try:
        data = json.loads(metadata_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""

    pending = data.get("pending_transcription") or {}
    media_id = pending.get("media_id")
    inbound_message_id = pending.get("inbound_message_id")
    if not media_id:
        return ""

    port = get_audio_transcription_port()
    request = TranscriptionRequest(
        media_id=media_id,
        mime_type=pending.get("mime_type", "audio/ogg"),
        voice_note=bool(pending.get("voice", True)),
        language_hint="es",
        max_duration_seconds=60,
    )
    result = await port.transcribe(request)

    # Limpiar pending_transcription — sea OK o error, no reintentar acá
    data.pop("pending_transcription", None)

    bus = get_event_bus()
    tenant_id = os.getenv("HUBARA_TENANT_ID", "hubara")

    if not result.ok or not result.text:
        # Persistir error para tracking
        errors = list(data.get("transcription_failures") or [])
        errors.append({
            "media_id": media_id,
            "error": result.error,
            "provider": result.provider,
        })
        data["transcription_failures"] = errors[-20:]
        _safe_write_metadata(metadata_file, data)

        # Avisar al cliente: pídele que escriba
        phone_number_id = data.get("phone_number_id") or os.getenv(
            "WHATSAPP_PHONE_NUMBER_ID"
        )
        to_number = session_id.replace(WHATSAPP_SESSION_PREFIX, "")
        if phone_number_id and to_number:
            try:
                if result.error == "too_long":
                    msg = (
                        "Recibí tu audio pero es muy largo para procesarlo "
                        "automáticamente. ¿Puedes contarme con mensajes "
                        "cortos o espera que te conecte con un asesor? 🤍"
                    )
                else:
                    msg = (
                        "Recibí tu audio pero no logré entenderlo bien. "
                        "¿Me lo escribes en un mensaje? 🤍"
                    )
                await wa_client.send_message(phone_number_id, to_number, msg)
            except Exception:  # noqa: BLE001
                pass

        try:
            await bus.record(make_wa_interaction(
                session_id=session_id,
                tenant_id=tenant_id,
                kind="audio_transcription_failed",
                component_id=media_id,
                wa_message_id=inbound_message_id,
                payload_extra={
                    "error": result.error,
                    "provider": result.provider,
                },
            ))
        except Exception:  # noqa: BLE001
            pass

        return ""

    # OK: persistir texto + emitir analytics
    transcriptions = list(data.get("recent_transcriptions") or [])
    transcriptions.append({
        "media_id": media_id,
        "text": result.text,
        "duration_seconds": result.duration_seconds,
        "provider": result.provider,
        "cost_usd_estimate": result.cost_usd_estimate,
    })
    data["recent_transcriptions"] = transcriptions[-20:]
    _safe_write_metadata(metadata_file, data)

    try:
        await bus.record(make_wa_interaction(
            session_id=session_id,
            tenant_id=tenant_id,
            kind="audio_transcribed",
            component_id=media_id,
            wa_message_id=inbound_message_id,
            payload_extra={
                "text_len": len(result.text),
                "duration_seconds": result.duration_seconds,
                "provider": result.provider,
                "cost_usd_estimate": result.cost_usd_estimate,
                "latency_ms": result.latency_ms,
            },
        ))
    except Exception:  # noqa: BLE001
        pass

    return result.text


def _safe_write_metadata(path, data: dict[str, Any]) -> None:
    try:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        activity.logger.warning(
            "transcribe_audio.write_failed", extra={"path": str(path)}
        )
