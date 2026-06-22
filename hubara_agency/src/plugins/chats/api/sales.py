"""Driving adapter HTTP del dominio Sales (FastAPI router).

Recibe el webhook de WhatsApp Cloud, parsea el body (parser puro), y delega al
`IngestInboundMessage` use case via el composition root. Cero filesystem aqui.

NOTA F9: el archivo se mantiene en `src/domains/sales_whatsapp/api.py` para
preservar el import path que usa `src/main.py`. Mover a
`interfaces/http/api.py` queda como follow-up explicito (PR aparte).
"""
from __future__ import annotations

import json

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from src.platform.config import WHATSAPP_APP_SECRET, WHATSAPP_VERIFY_TOKEN
from src.platform.observability.tracing import add_traced_background_task
from src.platform.whatsapp.webhook_security import verify_meta_signature
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

# Router PÚBLICO (sin JWT de Cognito): lo llama Meta, no el dashboard. Trae su
# propia auth — `hub.verify_token` (GET) + HMAC `X-Hub-Signature-256` (POST, via
# verify_meta_signature). `main.py` lee este marcador y NO le cuelga `require_auth`
# (que rompería la recepción de mensajes). El default de todo router es PROTEGIDO.
PUBLIC_ROUTER = True


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

    HU-WA24H-001 pre-mortem F9.2: verifica X-Hub-Signature-256 vía HMAC
    SHA256 antes de procesar. Bloquea inyección de fake statuses/messages
    que corromperían cost metrics o triggerían workflows fantasma.

    Si `WHATSAPP_APP_SECRET` está vacío (modo dev/local sin app real
    detrás), el handler logea warning pero NO rechaza — facilita desarrollo
    local con webhooks simulados. Pre-launch el operador DEBE setear el env
    var, sino el handler queda inseguro.
    """
    # 1. Leer RAW body antes de json parsing (HMAC se calcula sobre bytes
    #    exactos enviados por Meta — cualquier normalización rompe el hash).
    raw_body = await request.body()
    signature_header = request.headers.get("X-Hub-Signature-256")

    if WHATSAPP_APP_SECRET:
        if not verify_meta_signature(raw_body, signature_header, WHATSAPP_APP_SECRET):
            logger.warning(
                "webhook_signature_rejected",
                signature_header=signature_header[:30] if signature_header else None,
            )
            raise HTTPException(status_code=403, detail="invalid signature")
    else:
        logger.warning(
            "webhook_signature_verification_disabled",
            reason="WHATSAPP_APP_SECRET not configured — dev mode only",
        )

    # 2. Parsear body (validamos shape después de verificar autenticidad).
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        logger.warning("webhook_body_not_json", error=str(exc))
        raise HTTPException(status_code=400, detail=f"malformed body: {exc}")

    # 3. Statuses primero — no requieren ser mutuamente excluyentes con
    # messages (Meta podría enviarlos juntos).
    for status_update in parse_whatsapp_statuses(body):
        delivery_use_case = build_ingest_delivery_status_use_case()
        add_traced_background_task(
            background_tasks,
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
    add_traced_background_task(background_tasks, use_case.execute, parsed)
    return {"status": "ok"}
