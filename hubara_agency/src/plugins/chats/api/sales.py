"""Driving adapter HTTP del dominio Sales (FastAPI router).

Recibe el webhook de WhatsApp Cloud, parsea el body (parser puro), y delega al
`IngestInboundMessage` use case via el composition root. Cero filesystem aqui.

NOTA F9: el archivo se mantiene en `src/domains/sales_whatsapp/api.py` para
preservar el import path que usa `src/main.py`. Mover a
`interfaces/http/api.py` queda como follow-up explicito (PR aparte).
"""
from __future__ import annotations

import hmac
import json
import re

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

import src.platform.config as cfg
from src.platform.observability.tracing import add_traced_background_task
from src.platform.whatsapp.webhook_security import verify_meta_signature
from src.plugins.chats.agent.sales.composition import (
    build_ingest_delivery_status_use_case,
    build_ingest_handover_use_case,
    build_ingest_standby_use_case,
    build_ingest_use_case,
)
from src.plugins.chats.agent.sales.parsers import (
    HANDOVERS_FIELD,
    STANDBY_FIELD,
    parse_messaging_handovers,
    parse_whatsapp_inbound,
    parse_whatsapp_standby,
    parse_whatsapp_statuses,
    split_webhook_by_field,
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

    # SEC-15: comparación en tiempo constante + guarda de vacío (un verify token
    # vacío nunca matchea → `"" == ""` no bypasea).
    verify_token = cfg.WHATSAPP_VERIFY_TOKEN
    if (
        mode == "subscribe"
        and token
        and verify_token
        and hmac.compare_digest(token, verify_token)
    ):
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
    * `field == "standby"` (D1.4 MBA) — Meta Business Agent controla el
      hilo: `value.standby.{messages,message_echoes,statuses}`. Los dos
      primeros van a `IngestStandby` (vault, sin Temporal); los statuses al
      mismo `IngestDeliveryStatus` (costo de lo que MBA envió). NUNCA entra
      al ingest de Sales: no hay turno del bot mientras MBA responde.
    * `field == "messaging_handovers"` (D1.5 MBA) — cambio de control del
      hilo entre Business Agent y nuestra app: `IngestHandover` persiste
      `control_owner` en la sesión. Misma flag y misma lista cerrada que
      `standby`.

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

    app_secret = cfg.WHATSAPP_APP_SECRET
    # El placeholder de Terraform NO es un secreto real → tratarlo como ausente
    # (sino verificaríamos el HMAC de Meta contra el placeholder = 403 en TODO
    # webhook real, y el fail-closed `elif` quedaría inalcanzable).
    if app_secret and not cfg.is_placeholder(app_secret):
        if not verify_meta_signature(raw_body, signature_header, app_secret):
            logger.warning(
                "webhook_signature_rejected",
                signature_header=signature_header[:30] if signature_header else None,
            )
            raise HTTPException(status_code=403, detail="invalid signature")
    elif cfg.is_production():
        # FAIL-CLOSED (SEC-02): en prod, faltar WHATSAPP_APP_SECRET NO se
        # bypasea — el webhook RECHAZA en vez de procesar payloads sin verificar
        # (inyección de mensajes/statuses falsos que corromperían cost metrics
        # o arrancarían workflows fantasma).
        logger.error(
            "webhook_signature_secret_missing_in_prod",
            reason="WHATSAPP_APP_SECRET no configurado en producción",
        )
        raise HTTPException(status_code=403, detail="webhook secret not configured")
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

    # 3. Strategy por `field` (D1.4): cada campo del webhook tiene su handler
    # y ve SOLO sus `changes`. El de `messages` es el código de siempre,
    # intacto; los demás son aditivos. Sin `field` (payloads simulados /
    # legacy) todo cuenta como `messages`. Un campo sin handler se acepta con
    # 200 (Meta no reintenta) y se loguea.
    for field_name, part in split_webhook_by_field(body).items():
        handler = _FIELD_HANDLERS.get(field_name)
        if handler is None:
            logger.info("webhook_field_ignored", field=field_name)
            continue
        handler(part, background_tasks)
    return {"status": "ok"}


# ── handlers por `field` ───────────────────────────────────────────────────────

def _handle_messages(body: dict, background_tasks: BackgroundTasks) -> None:
    """El path productivo de siempre (inbound del cliente + statuses)."""
    # Statuses primero — no requieren ser mutuamente excluyentes con
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
        return

    use_case = build_ingest_use_case()
    add_traced_background_task(background_tasks, use_case.execute, parsed)


def _handle_standby(body: dict, background_tasks: BackgroundTasks) -> None:
    """D1.4: Meta Business Agent controla el hilo. Detrás de la flag
    `MBA_STANDBY_ENABLED` (default OFF: se acepta y se descarta, como hasta
    hoy); la lista cerrada de clientes la aplica `IngestStandby`."""
    if not cfg.MBA_STANDBY_ENABLED:
        logger.info("webhook_standby_ignored", reason="MBA_STANDBY_ENABLED apagado")
        return
    standby = parse_whatsapp_standby(body)
    if standby is None:
        return
    for status_update in standby.statuses:
        add_traced_background_task(
            background_tasks,
            build_ingest_delivery_status_use_case().execute,
            status_update.wa_message_id,
            status_update.status,
            status_update.pricing,
        )
    logger.info(
        "webhook_standby",
        messages=len(standby.messages),
        echoes=len(standby.echoes),
        statuses=len(standby.statuses),
    )
    if standby.messages or standby.echoes:
        add_traced_background_task(
            background_tasks, build_ingest_standby_use_case().execute, standby
        )


def _handle_messaging_handovers(body: dict, background_tasks: BackgroundTasks) -> None:
    """D1.5: quién controla el hilo. Detrás de la misma flag que `standby`
    (default OFF: se acepta y se descarta); la lista cerrada la aplica
    `IngestHandover`. Un ítem con shape desconocido se loguea con el body
    (la referencia de Meta para WhatsApp no está publicada: verificar en F0)."""
    if not cfg.MBA_STANDBY_ENABLED:
        logger.info("webhook_messaging_handovers_ignored", reason="MBA_STANDBY_ENABLED apagado")
        return
    event = parse_messaging_handovers(body)
    if event is None:
        return
    if event.unparsed:
        # El body va al log para descubrir el shape real en F0, con los
        # teléfonos enmascarados (mismo criterio que el resto: ***últimos 4).
        logger.warning("webhook_messaging_handovers_unparsed", unparsed=event.unparsed, body=_mask_phones(body))
    logger.info("webhook_messaging_handovers", handovers=len(event.handovers), unparsed=event.unparsed)
    if event.handovers:
        add_traced_background_task(background_tasks, build_ingest_handover_use_case().execute, event)


_LONG_DIGITS = re.compile(r"\d{8,}")


def _mask_phones(body: dict) -> str:
    """JSON del body con toda tira de ≥8 dígitos reducida a ``***<últimos 4>``."""
    return _LONG_DIGITS.sub(lambda m: f"***{m.group(0)[-4:]}", json.dumps(body, ensure_ascii=False))


_FIELD_HANDLERS = {
    "messages": _handle_messages,
    STANDBY_FIELD: _handle_standby,
    HANDOVERS_FIELD: _handle_messaging_handovers,
}
