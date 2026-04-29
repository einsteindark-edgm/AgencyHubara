"""Use case top-level del webhook de WhatsApp.

Recibe un `WhatsAppMessage` ya parseado (la decision de 4xx vs 200 vive en el
parser, en interfaces/http) y orquesta:

1. Filtrar mensajes sin texto (media-only).
2. Persistir el evento del usuario en el JSONL via `MessageHistoryStorePort`.
3. Delegar a `LoadOrStartSalesSession` para resolver ruta + signal.

Reemplaza al legado `service.process_incoming_message` + `_signal_temporal_and_poll`,
con la diferencia de que NO toca filesystem ni Temporal directamente: todo va por
puertos / use cases inyectados.
"""
from __future__ import annotations

import structlog

from src.core.constants import WHATSAPP_SESSION_PREFIX
from src.domains.sales_whatsapp.application.ports.message_history_store import (
    MessageHistoryStorePort,
)
from src.domains.sales_whatsapp.application.use_cases.load_or_start_sales_session import (
    LoadOrStartSalesSession,
)
from src.domains.sales_whatsapp.parsers import WhatsAppMessage

logger = structlog.get_logger()


class IngestInboundMessage:
    """Procesa un `WhatsAppMessage` ya parseado: history + routing + signal."""

    def __init__(
        self,
        history_store: MessageHistoryStorePort,
        load_session: LoadOrStartSalesSession,
    ) -> None:
        self._history_store = history_store
        self._load_session = load_session

    async def execute(self, parsed: WhatsAppMessage) -> None:
        if parsed.text is None:
            logger.info(
                "Non-text WhatsApp message ignored",
                message_type=(parsed.media or {}).get("type"),
            )
            return

        session_id = f"{WHATSAPP_SESSION_PREFIX}{parsed.from_number}"
        logger.info(
            "WhatsApp Message Received",
            message_text=parsed.text,
            from_number=parsed.from_number,
        )

        # 1. Asegurar el log de entrada del cliente antes de ir a memoria viva (Temporal).
        self._history_store.append_user_event(session_id, parsed.text)

        # 2. Resolver ruta + signal al workflow correspondiente.
        await self._load_session.execute(
            session_id=session_id,
            message=parsed.text,
            phone_number_id=parsed.phone_number_id,
        )
