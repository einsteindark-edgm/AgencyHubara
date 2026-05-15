"""Activity que envia mensajes de texto a WhatsApp.

El `name=` del decorator se preserva (`send_whatsapp_message_activity`) para no
invalidar history events de workflows en vuelo. Solo cambia el path de import.

`send_message_to_session` es la version pura (sin decorators de Temporal) que
los handlers HTTP (dashboard handoff) reutilizan para mandar mensajes del
humano al cliente sin pasar por el worker. La activity la envuelve.
"""
from __future__ import annotations

import asyncio
import json
import os

from temporalio import activity

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.constants import WHATSAPP_SESSION_PREFIX
from src.platform.temporal.heartbeat import with_heartbeat
from src.platform.whatsapp import client as whatsapp_client


async def send_message_to_session(session_id: str, message: str) -> None:
    """Envia `message` al cliente cuyo `session_id` mapea a un numero de WhatsApp.

    Pura (no toca Temporal). Resuelve `phone_number_id` desde `metadata.json` o
    desde `WHATSAPP_PHONE_NUMBER_ID` env var (fallback). Fragmenta el mensaje en
    burbujas separadas por `\\n\\n` con una pausa de 1.5s entre chunks (igual que
    la activity, para preservar UX en WhatsApp).

    Reutilizada por:
      * `send_whatsapp_message_activity` (worker, dentro de workflows).
      * `dashboard/handoff.py` (HTTP, mensajes del humano operador).
    """
    from_number = session_id.replace(WHATSAPP_SESSION_PREFIX, "")

    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    if not phone_number_id:
        raise RuntimeError("WHATSAPP_PHONE_NUMBER_ID not configured")

    try:
        metadata_file = WORKSPACE_VAULT_DIR / session_id / "metadata.json"
        if metadata_file.exists():
            data = json.loads(metadata_file.read_text(encoding="utf-8"))
            phone_number_id = data.get("phone_number_id", phone_number_id)
    except (OSError, json.JSONDecodeError):
        pass

    chunks = [chunk.strip() for chunk in message.split("\n\n") if chunk.strip()]
    for chunk in chunks:
        await whatsapp_client.send_message(phone_number_id, from_number, chunk)
        await asyncio.sleep(1.5)


@activity.defn(name="send_whatsapp_message_activity")
@with_heartbeat(every=10)
async def send_whatsapp_message_activity(session_id: str, message: str) -> None:
    await send_message_to_session(session_id, message)


async def _read_metadata_for_typing(session_id: str) -> tuple[str | None, str | None]:
    """Devuelve `(phone_number_id, last_inbound_message_id)` desde metadata.

    Fallback de phone_number_id: env var. message_id NO tiene fallback — sin
    referencia a un mensaje del cliente la API de WhatsApp rechaza la request.
    """
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    message_id: str | None = None
    try:
        metadata_file = WORKSPACE_VAULT_DIR / session_id / "metadata.json"
        if metadata_file.exists():
            data = json.loads(metadata_file.read_text(encoding="utf-8"))
            phone_number_id = data.get("phone_number_id", phone_number_id)
            message_id = data.get("last_inbound_message_id")
    except (OSError, json.JSONDecodeError):
        pass
    return phone_number_id, message_id


@activity.defn(name="send_typing_indicator_activity")
async def send_typing_indicator_activity(session_id: str) -> None:
    """Dispara el "escribiendo..." outbound al cliente.

    Lee `last_inbound_message_id` desde `metadata.json` (escrito por
    `IngestInboundMessage` al recibir cada webhook). Si no hay message_id
    — caso turno proactivo / handoff / ghost trigger — la activity noopea
    silenciosamente: la API de WhatsApp requiere referenciar un mensaje del
    cliente, y un typing indicator sin contexto fresco no aporta UX.

    Best-effort: errores HTTP los absorbe `client.send_typing_indicator`.
    Sin heartbeat porque es una request corta (timeout 4s) y el retry seria
    contraproducente: si fallo, el LLM ya esta procesando y el typing
    seria stale.
    """
    phone_number_id, message_id = await _read_metadata_for_typing(session_id)
    if not phone_number_id or not message_id:
        return
    await whatsapp_client.send_typing_indicator(phone_number_id, message_id)
