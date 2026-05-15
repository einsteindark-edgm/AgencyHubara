"""Use case top-level del webhook de WhatsApp.

Recibe un `WhatsAppMessage` ya parseado (la decision de 4xx vs 200 vive en el
parser, en ``api.py``) y orquesta:

1. Filtrar mensajes sin texto (media-only).
2. Persistir el evento del usuario en el JSONL via ``FilesystemMessageHistoryStore``.
3. Delegar a ``LoadOrStartSalesSession`` para resolver ruta + signal.

Reemplaza al legado ``service.process_incoming_message`` +
``_signal_temporal_and_poll``, con la diferencia de que NO toca filesystem ni
Temporal directamente: todo va por adapters / use cases inyectados.

PR-E: el ``MessageHistoryStorePort`` Protocol intermedio desaparecio. El use
case ahora type-hints la concreta ``FilesystemMessageHistoryStore`` directo
(Python sigue siendo duck-typed, asi que los fakes en tests pasan sin
isinstance check). A esta escala (1 implementacion concreta, 2 fakes en
tests) el Protocol agregaba indireccion sin testabilidad nueva.
"""
from __future__ import annotations

import structlog

from src.platform.constants import WHATSAPP_SESSION_PREFIX
from src.platform.state import FilesystemMetadataStore
from src.sales_whatsapp.parsers import WhatsAppMessage
from src.platform.session_history import FilesystemMessageHistoryStore
from src.sales_whatsapp.use_cases.load_or_start_sales_session import (
    LoadOrStartSalesSession,
)

logger = structlog.get_logger()


class IngestInboundMessage:
    """Procesa un `WhatsAppMessage` ya parseado: history + routing + signal."""

    def __init__(
        self,
        history_store: FilesystemMessageHistoryStore,
        load_session: LoadOrStartSalesSession,
        metadata_store: FilesystemMetadataStore,
    ) -> None:
        self._history_store = history_store
        self._load_session = load_session
        self._metadata_store = metadata_store

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

        # 2. Persistir el message_id en metadata para que el typing indicator
        #    outbound pueda referenciarlo. Fix 5: la API de WhatsApp requiere
        #    un message_id del cliente para mostrar "escribiendo..." y aprovecha
        #    para marcar el mensaje como leido (doble UX win).
        #    Si el parser no extrajo message_id (caso raro), seguimos sin
        #    typing — el workflow funciona, solo no muestra el indicador.
        if parsed.message_id:
            try:
                data = self._metadata_store.read(session_id)
                data["last_inbound_message_id"] = parsed.message_id
                self._metadata_store.write(session_id, data)
            except Exception:  # noqa: BLE001 - best-effort; no rompe el flujo
                logger.info("metadata write for typing indicator failed (ignored)")

        # 3. Resolver ruta + signal al workflow correspondiente.
        await self._load_session.execute(
            session_id=session_id,
            message=parsed.text,
            phone_number_id=parsed.phone_number_id,
        )
