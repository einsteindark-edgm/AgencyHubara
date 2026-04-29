"""Port: WhatsAppGatewayPort.

Describe la capability de enviar mensajes outbound a un canal de WhatsApp
(o fake/echo en dev). La activity `send_whatsapp_message_activity` invoca
una implementacion concreta de este puerto.

El adapter default (`DefaultWhatsAppGateway`) envuelve `httpx` contra la API
oficial de WhatsApp Cloud. En tests, puede sustituirse por un fake que
acumule los envios en memoria.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class WhatsAppGatewayPort(Protocol):
    """Envia un mensaje de texto plano a un destinatario via canal WhatsApp."""

    async def send_message(self, phone_number_id: str, to: str, text: str) -> None:
        """Envia `text` a `to` usando la cuenta `phone_number_id`.

        Implementaciones reales pueden hacer I/O de red; el contrato es que la
        coroutine completa cuando el envio fue aceptado (o registrado en logs
        si no hay token configurado).
        """
        ...
