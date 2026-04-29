"""Facade DEPRECATED de back-compat.

Toda la logica vive ahora en `application/use_cases/` (DEHA: F9 cierra NEW-2).
Este modulo solo re-exporta `process_incoming_message` como una funcion modulo
para que los call-sites antiguos (background_tasks.add_task, tests legados,
imports indirectos) sigan funcionando sin cambios.

Para codigo nuevo: importar `IngestInboundMessage` desde
`src.domains.sales_whatsapp.application.use_cases` o pedir el use case via
`composition.build_ingest_use_case()`.
"""
from __future__ import annotations

from src.domains.sales_whatsapp.composition import build_ingest_use_case
from src.domains.sales_whatsapp.parsers import WhatsAppMessage


async def process_incoming_message(parsed: WhatsAppMessage) -> None:
    """Despacha un mensaje ya parseado al workflow Temporal correspondiente.

    Wrapper de back-compat: delega al `IngestInboundMessage` use case construido
    por el composition root. La logica de filesystem y routing vive en
    `application/use_cases/` y `infrastructure/storage/`.
    """
    use_case = build_ingest_use_case()
    await use_case.execute(parsed)
