"""El camino entero del webhook en un test (entrada durable, ADR 2026-10-15):
lo que el webhook deja en Temporal llega al ingest como lo haría la activity
del worker — serializado y rehidratado, con la dirección de su conversación."""
from __future__ import annotations

from typing import Any

from src.plugins.chats.agent.sales.contracts import InboundMessageInput
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage


class InlineInboundEntry:
    def __init__(self, ingest: Any) -> None:
        self.ingest = ingest
        self.inputs: list[InboundMessageInput] = []

    async def accept(self, inp: InboundMessageInput) -> bool:
        self.inputs.append(inp)
        await self.ingest.execute(WhatsAppMessage(**inp.message))
        return True
