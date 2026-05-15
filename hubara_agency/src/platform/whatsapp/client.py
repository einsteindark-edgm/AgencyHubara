"""Cliente HTTP puro hacia WhatsApp Cloud API.

Driven adapter sin dependencias de Temporal. La activity que invoca a este cliente
vive en `src/core/infrastructure/whatsapp/activities.py`.
"""
from __future__ import annotations

import httpx
import structlog

from src.platform.config import WHATSAPP_ACCESS_TOKEN, WHATSAPP_API_URL

logger = structlog.get_logger()


async def send_message(phone_number_id: str, to: str, text: str) -> None:
    """Envía un mensaje de texto plano a un usuario a través del API Oficial de WhatsApp Cloud."""
    if not WHATSAPP_ACCESS_TOKEN:
        logger.warning("Fake Send", to=to, text=text)
        return

    url = WHATSAPP_API_URL.format(phone_number_id=phone_number_id)
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    data = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=data)
        if response.status_code == 200:
            logger.info("WhatsApp Reply OK", to=to)
        else:
            logger.error("Failed to reply", response_text=response.text)


async def send_typing_indicator(
    phone_number_id: str,
    message_id: str,
) -> None:
    """Muestra "escribiendo..." al cliente en su chat de WhatsApp.

    API WhatsApp Cloud (octubre 2024): el indicador se envia marcando un
    mensaje del cliente como leido + `typing_indicator.type=text`. El cliente
    ve la animacion durante ~25s o hasta que el negocio mande otro mensaje
    (lo que ocurra primero).

    Es best-effort: si no hay `message_id` (caso turno sintetico sin contexto
    de mensaje real) o el token no esta configurado, noopea silenciosamente.
    Errores HTTP se loguean pero no se re-raisen — un typing fallido no debe
    bloquear el turno del agente.
    """
    if not WHATSAPP_ACCESS_TOKEN or not message_id:
        return

    url = WHATSAPP_API_URL.format(phone_number_id=phone_number_id)
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    data = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {"type": "text"},
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=data, timeout=4.0)
            if response.status_code != 200:
                logger.info(
                    "Typing indicator failed (best-effort, ignored)",
                    status=response.status_code,
                )
    except Exception as e:  # noqa: BLE001 - best-effort
        logger.info("Typing indicator request failed (ignored)", error=str(e))
