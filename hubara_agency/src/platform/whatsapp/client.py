"""Cliente HTTP puro hacia WhatsApp Cloud API.

Driven adapter sin dependencias de Temporal. La activity que invoca a este
cliente vive en `src/platform/whatsapp/activities.py`.

Cobertura de tipos de mensaje:

* Texto: `send_message` (legacy, mantiene firma original para no romper la
  activity existente)
* Typing: `send_typing_indicator`
* Media: `send_image`, `send_audio`, `send_video`, `send_document`
* Interactive: `send_interactive_buttons`, `send_interactive_list`,
  `send_cta_url`, `send_location_request`, `send_flow`
* Catalog (requiere Meta Catalog): `send_product`, `send_product_list`,
  `send_order_details`
* Nativos: `send_location`, `send_contact`, `send_reaction`
* Reply contextual: cualquier `send_*` acepta `reply_to_message_id` para
  citar el mensaje del cliente (Meta `context.message_id`)
* Atribución: cada función devuelve `OutboundResult(wa_message_id, ok, error)`
  para que la capa de analytics centralizada (`src/platform/analytics/`)
  pueda correlacionar outbound con interacción posterior.

Cobertura DEHA:
  * R-DET safe — no es activity, es adapter puro que llama desde activities.
  * R-STATELESS — sin module-level cache (excepto el global config WHATSAPP_*).
  * Sin imports de Temporal acá.
"""
from __future__ import annotations

from typing import Any

import httpx
import structlog

from src.platform.config import WHATSAPP_ACCESS_TOKEN, WHATSAPP_API_URL
from src.platform.whatsapp import dtos as wa_dtos
from src.platform.whatsapp import outbound as wa_outbound

logger = structlog.get_logger()


# =============================================================================
# Legacy text send (mantenida con firma original)
# =============================================================================


async def send_message(phone_number_id: str, to: str, text: str) -> None:
    """Envía un mensaje de texto plano. Firma legacy preservada para
    `send_whatsapp_message_activity`."""
    if not WHATSAPP_ACCESS_TOKEN:
        logger.warning("Fake Send", to=to, text=text)
        return

    data = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    result = await _post_json(phone_number_id, data, label="text")
    if not result.ok:
        logger.error("Failed to reply", error=result.error)


async def send_text(
    phone_number_id: str,
    to: str,
    text: str,
    reply_to_message_id: str | None = None,
) -> wa_dtos.OutboundResult:
    """Envía un mensaje de texto plano y devuelve `OutboundResult`.

    A diferencia del legacy `send_message` (que devuelve `None` y se usa
    desde `send_whatsapp_message_activity`), este shape uniforme se usa
    desde `flush_pending_ui_intents_activity` — donde necesitamos
    `OutboundResult.ok` para la idempotencia (pop+write por intent) y
    `wa_message_id` para correlación analytics.

    Soporta `reply_to_message_id` para citar el mensaje del cliente
    (Meta `context.message_id`).
    """
    data: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    if reply_to_message_id:
        data["context"] = {"message_id": reply_to_message_id}
    return await _post_json(phone_number_id, data, label="text")


async def send_typing_indicator(
    phone_number_id: str,
    message_id: str,
) -> None:
    """Muestra "escribiendo..." al cliente (firma legacy preservada).

    Best-effort: si no hay `message_id` o token, noopea silenciosamente.
    Errores HTTP se loguean pero no se re-raisean.
    """
    if not WHATSAPP_ACCESS_TOKEN or not message_id:
        return

    url = WHATSAPP_API_URL.format(phone_number_id=phone_number_id)
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    data = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {"type": "text"},
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=data, timeout=4.0)
            if response.status_code != 200:
                logger.info(
                    "Typing indicator failed (best-effort, ignored)",
                    status=response.status_code,
                )
    except Exception as e:  # noqa: BLE001 - best-effort
        logger.info("Typing indicator request failed (ignored)", error=str(e))


# =============================================================================
# Media — image / audio / video / document
# =============================================================================


async def send_image(
    phone_number_id: str,
    to: str,
    payload: wa_dtos.ImageOutbound,
    reply_to_message_id: str | None = None,
) -> wa_dtos.OutboundResult:
    data = wa_outbound.build_image(to, payload, reply_to_message_id)
    return await _post_json(phone_number_id, data, label="image")


async def send_audio(
    phone_number_id: str,
    to: str,
    payload: wa_dtos.AudioOutbound,
    reply_to_message_id: str | None = None,
) -> wa_dtos.OutboundResult:
    data = wa_outbound.build_audio(to, payload, reply_to_message_id)
    return await _post_json(phone_number_id, data, label="audio")


async def send_video(
    phone_number_id: str,
    to: str,
    payload: wa_dtos.VideoOutbound,
    reply_to_message_id: str | None = None,
) -> wa_dtos.OutboundResult:
    data = wa_outbound.build_video(to, payload, reply_to_message_id)
    return await _post_json(phone_number_id, data, label="video")


async def send_document(
    phone_number_id: str,
    to: str,
    payload: wa_dtos.DocumentOutbound,
    reply_to_message_id: str | None = None,
) -> wa_dtos.OutboundResult:
    data = wa_outbound.build_document(to, payload, reply_to_message_id)
    return await _post_json(phone_number_id, data, label="document")


# =============================================================================
# Interactive — reply buttons / list / cta_url / location_request / flow
# =============================================================================


async def send_interactive_buttons(
    phone_number_id: str,
    to: str,
    payload: wa_dtos.InteractiveButtonsOutbound,
    reply_to_message_id: str | None = None,
) -> wa_dtos.OutboundResult:
    data = wa_outbound.build_interactive_buttons(to, payload, reply_to_message_id)
    return await _post_json(phone_number_id, data, label="interactive.button")


async def send_interactive_list(
    phone_number_id: str,
    to: str,
    payload: wa_dtos.InteractiveListOutbound,
    reply_to_message_id: str | None = None,
) -> wa_dtos.OutboundResult:
    data = wa_outbound.build_interactive_list(to, payload, reply_to_message_id)
    return await _post_json(phone_number_id, data, label="interactive.list")


async def send_cta_url(
    phone_number_id: str,
    to: str,
    payload: wa_dtos.InteractiveCTAUrlOutbound,
    reply_to_message_id: str | None = None,
) -> wa_dtos.OutboundResult:
    data = wa_outbound.build_cta_url(to, payload, reply_to_message_id)
    return await _post_json(phone_number_id, data, label="interactive.cta_url")


async def send_location_request(
    phone_number_id: str,
    to: str,
    payload: wa_dtos.InteractiveLocationRequestOutbound,
    reply_to_message_id: str | None = None,
) -> wa_dtos.OutboundResult:
    data = wa_outbound.build_location_request(to, payload, reply_to_message_id)
    return await _post_json(phone_number_id, data, label="interactive.location_request")


async def send_flow(
    phone_number_id: str,
    to: str,
    payload: wa_dtos.InteractiveFlowOutbound,
    reply_to_message_id: str | None = None,
) -> wa_dtos.OutboundResult:
    data = wa_outbound.build_flow(to, payload, reply_to_message_id)
    return await _post_json(phone_number_id, data, label="interactive.flow")


# =============================================================================
# Catalog-dependent — product / product_list / order_details
# =============================================================================


async def send_product(
    phone_number_id: str,
    to: str,
    payload: wa_dtos.InteractiveProductOutbound,
    reply_to_message_id: str | None = None,
) -> wa_dtos.OutboundResult:
    data = wa_outbound.build_product(to, payload, reply_to_message_id)
    return await _post_json(phone_number_id, data, label="interactive.product")


async def send_product_list(
    phone_number_id: str,
    to: str,
    payload: wa_dtos.InteractiveProductListOutbound,
    reply_to_message_id: str | None = None,
) -> wa_dtos.OutboundResult:
    data = wa_outbound.build_product_list(to, payload, reply_to_message_id)
    return await _post_json(phone_number_id, data, label="interactive.product_list")


async def send_order_details(
    phone_number_id: str,
    to: str,
    payload: wa_dtos.InteractiveOrderDetailsOutbound,
    reply_to_message_id: str | None = None,
) -> wa_dtos.OutboundResult:
    data = wa_outbound.build_order_details(to, payload, reply_to_message_id)
    return await _post_json(phone_number_id, data, label="interactive.order_details")


# =============================================================================
# Native — location / contacts / reaction
# =============================================================================


async def send_location(
    phone_number_id: str,
    to: str,
    payload: wa_dtos.LocationOutbound,
    reply_to_message_id: str | None = None,
) -> wa_dtos.OutboundResult:
    data = wa_outbound.build_location(to, payload, reply_to_message_id)
    return await _post_json(phone_number_id, data, label="location")


async def send_contact(
    phone_number_id: str,
    to: str,
    contacts: list[wa_dtos.ContactCard],
    reply_to_message_id: str | None = None,
) -> wa_dtos.OutboundResult:
    data = wa_outbound.build_contacts(to, contacts, reply_to_message_id)
    return await _post_json(phone_number_id, data, label="contacts")


async def send_reaction(
    phone_number_id: str,
    to: str,
    payload: wa_dtos.ReactionOutbound,
) -> wa_dtos.OutboundResult:
    """Reactions NO aceptan context (`reply_to_message_id`) — su propio
    `message_id` ya referencia el mensaje del cliente."""
    data = wa_outbound.build_reaction(to, payload)
    return await _post_json(phone_number_id, data, label="reaction")


# =============================================================================
# Templates (HU-WA24H-001 F1.7)
# =============================================================================


async def send_template(
    phone_number_id: str,
    to: str,
    spec: Any,  # TemplateSpec — forward typing por evitar import cycle
    variables: dict[str, str],
) -> wa_dtos.OutboundResult:
    """Envía un template aprobado por Meta.

    Construye el payload con `outbound.build_template_message` (que valida
    variables vs spec) y POST a la Cloud API. Templates NO aceptan
    `reply_to_message_id` — son business-initiated, no replies.

    El `OutboundResult` retornado tiene `wa_message_id` para correlación
    con el webhook `message_status` que después trae `pricing` + cost.
    """
    data = wa_outbound.build_template_message(to, spec, variables)
    return await _post_json(phone_number_id, data, label="template")


# =============================================================================
# Internal: POST helper
# =============================================================================


async def _post_json(
    phone_number_id: str,
    data: dict[str, Any],
    label: str,
) -> wa_dtos.OutboundResult:
    """POST genérico a la API de WhatsApp Cloud.

    Devuelve `OutboundResult` con:
      * wa_message_id: el id que Meta asignó al mensaje outbound (para
        correlación analytics)
      * ok: True si status 200
      * error: detalle si falló

    Si `WHATSAPP_ACCESS_TOKEN` no está configurado (dev sin token), simula
    un envío exitoso con id sintético y loguea como `FakeSend`.
    """
    if not WHATSAPP_ACCESS_TOKEN:
        logger.warning("FakeSend (no token configured)", label=label, payload=data)
        return wa_dtos.OutboundResult(wa_message_id=f"fake-{label}", ok=True)

    url = WHATSAPP_API_URL.format(phone_number_id=phone_number_id)
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, headers=headers, json=data)
    except httpx.HTTPError as e:
        logger.error("WhatsApp send transport error", label=label, error=str(e))
        return wa_dtos.OutboundResult(wa_message_id=None, ok=False, error=str(e))

    if response.status_code != 200:
        logger.error(
            "WhatsApp send failed",
            label=label,
            status=response.status_code,
            body=response.text,
        )
        return wa_dtos.OutboundResult(
            wa_message_id=None,
            ok=False,
            error=f"http_{response.status_code}: {response.text[:300]}",
        )

    body = response.json()
    msg_id: str | None = None
    try:
        msg_id = body["messages"][0]["id"]
    except (KeyError, IndexError, TypeError):
        pass

    logger.info("WhatsApp send OK", label=label, wa_message_id=msg_id)
    return wa_dtos.OutboundResult(wa_message_id=msg_id, ok=True)
