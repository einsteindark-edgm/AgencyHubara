"""Driving adapter HTTP del dominio Sales (FastAPI router).

Recibe el webhook de WhatsApp Cloud, parsea el body (parser puro), y delega al
`IngestInboundMessage` use case via el composition root. Cero filesystem aqui.

NOTA F9: el archivo se mantiene en `src/domains/sales_whatsapp/api.py` para
preservar el import path que usa `src/main.py`. Mover a
`interfaces/http/api.py` queda como follow-up explicito (PR aparte).
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from src.platform.config import WHATSAPP_VERIFY_TOKEN
from src.plugins.chats.agent.sales.composition import (
    build_ingest_delivery_status_use_case,
    build_ingest_use_case,
)
from src.plugins.chats.agent.sales.parsers import (
    parse_whatsapp_inbound,
    parse_whatsapp_statuses,
)

logger = structlog.get_logger()

router = APIRouter()


@router.get("/webhook")
async def verify_webhook(request: Request):
    """WhatsApp verification endpoint."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        logger.info("WhatsApp Webhook Verified")
        return int(challenge)
    raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/webhook")
async def handle_whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receives JSON body directly and frees connection to prevent Timeout.

    Dos clases de eventos pueden venir en el mismo body (Meta los puede
    mezclar pero típicamente un body trae UNO):

    * `entry[*].changes[*].value.messages[]` — inbound del cliente.
      Delegado a `IngestInboundMessage` (legacy path).
    * `entry[*].changes[*].value.statuses[]` — delivery status de un
      outbound nuestro (HU-WA24H-001 F1.10). Delegado a
      `IngestDeliveryStatus` para materializar cost + summary.

    Ambos handlers corren como background tasks — devolvemos 200 al toque
    para evitar timeout de Meta.
    """
    body = await request.json()

    # Statuses primero — no requieren ser mutuamente excluyentes con
    # messages (Meta podría enviarlos juntos).
    for status_update in parse_whatsapp_statuses(body):
        delivery_use_case = build_ingest_delivery_status_use_case()
        background_tasks.add_task(
            delivery_use_case.execute,
            status_update.wa_message_id,
            status_update.status,
            status_update.pricing,
        )

    try:
        parsed = parse_whatsapp_inbound(body)
    except ValueError as exc:
        logger.warning("Malformed WhatsApp webhook body", error=str(exc))
        raise HTTPException(status_code=400, detail=f"malformed payload: {exc}")

    if parsed is None:
        # Sin messages[] — ya despachamos los statuses arriba (si había).
        return {"status": "ok"}

    use_case = build_ingest_use_case()
    background_tasks.add_task(use_case.execute, parsed)
    return {"status": "ok"}
