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
