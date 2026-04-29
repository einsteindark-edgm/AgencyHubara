"""Adapter default para `WhatsAppGatewayPort`: delega en `whatsapp.client.send_message`.

Wrapper fino para satisfacer el Protocol via `isinstance` con `@runtime_checkable`.
La logica HTTP real sigue viviendo en `src/core/infrastructure/whatsapp/client.py`.
"""
from __future__ import annotations

from src.core.infrastructure.whatsapp import client as whatsapp_client


class DefaultWhatsAppGateway:
    """Envia mensajes WhatsApp via httpx contra la API Cloud oficial."""

    async def send_message(self, phone_number_id: str, to: str, text: str) -> None:
        await whatsapp_client.send_message(phone_number_id, to, text)
