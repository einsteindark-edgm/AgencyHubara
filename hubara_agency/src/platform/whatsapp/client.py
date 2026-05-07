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
