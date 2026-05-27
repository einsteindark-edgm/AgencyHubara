"""Tipos de eventos analytics + constructors.

Diseño:

* Cada evento es un `AnalyticsEvent` frozen dataclass — R-JSON safe.
* `category` agrupa por dominio (wa_inbound, wa_outbound, conversion,
  referral, error).
* `kind` es el discriminador específico (button_click, list_select,
  product_card_send, add_to_cart, purchase, ctwa_attribution, ...).
* `payload` es libre — cada constructor define qué meter y los sinks
  saben cómo serializar.
* `correlation`: trazabilidad cruzada (session_id, tenant_id, ctwa_clid,
  wa_message_id, reference_id de orden, etc.).
* `timestamp_ms` es epoch ms — set por el constructor, no inyectable
  para no violar R-DET en workflows. Las activities pueden setearlo
  o dejar el default.

Los constructors `make_*` están diseñados para que el caller no piense en
schema: solo pasa los datos del dominio y obtiene el evento listo.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

EventCategory = Literal[
    "wa_inbound",
    "wa_outbound",
    "conversion",
    "referral",
    "error",
    "system",
]

EventKind = str  # snake_case, ej "button_click", "list_select", "purchase"


@dataclass(frozen=True)
class AnalyticsEvent:
    """Evento analytics estándar.

    Atributos:
      event_id: UUID4, único por evento (idempotencia en sinks).
      timestamp_ms: epoch ms — el bus lo setea si caller lo dejó en 0.
      category: dominio del evento.
      kind: tipo específico dentro del dominio.
      correlation: identificadores cross-evento (session, tenant, msg_id,
        ctwa_clid, reference_id).
      payload: datos específicos del evento.
      version: schema version (incrementar si cambia shape de `payload`).
    """

    event_id: str
    timestamp_ms: int
    category: EventCategory
    kind: EventKind
    correlation: dict[str, Any]
    payload: dict[str, Any]
    version: int = 1
    tags: list[str] = field(default_factory=list)


def _new_id() -> str:
    return uuid.uuid4().hex


def _now_ms() -> int:
    return int(time.time() * 1000)


# =============================================================================
# Constructors
# =============================================================================


def make_referral_captured(
    session_id: str,
    tenant_id: str | None,
    referral: dict[str, Any],
    inbound_message_id: str | None,
) -> AnalyticsEvent:
    """Evento emitido la primera vez que detectamos un `referral` en un inbound.

    Es el punto de entrada de la atribución CTWA — desde acá los sinks
    deciden si lo POSTean a Meta Conversions API (event_name='Lead' o
    'InitiateMessaging' según convención).
    """
    return AnalyticsEvent(
        event_id=_new_id(),
        timestamp_ms=_now_ms(),
        category="referral",
        kind="ctwa_referral_captured",
        correlation={
            "session_id": session_id,
            "tenant_id": tenant_id,
            "ctwa_clid": referral.get("ctwa_clid"),
            "source_id": referral.get("source_id"),
            "source_type": referral.get("source_type"),
            "inbound_message_id": inbound_message_id,
        },
        payload={
            "source_url": referral.get("source_url"),
            "source_id": referral.get("source_id"),
            "source_type": referral.get("source_type"),
            "headline": referral.get("headline"),
            "body": referral.get("body"),
            "media_type": referral.get("media_type"),
            "image_url": referral.get("image_url"),
            "video_url": referral.get("video_url"),
            "thumbnail_url": referral.get("thumbnail_url"),
            "referred_product": referral.get("referred_product"),
            "ctwa_clid": referral.get("ctwa_clid"),
        },
        tags=["referral", "attribution", "ctwa"],
    )


def make_wa_interaction(
    *,
    session_id: str,
    tenant_id: str | None,
    kind: EventKind,  # "button_click" | "list_select" | "flow_submit" | ...
    component_id: str | None,
    component_title: str | None = None,
    wa_message_id: str | None,
    payload_extra: dict[str, Any] | None = None,
) -> AnalyticsEvent:
    """Click / selección / submit del cliente sobre un componente UI.

    Ejemplos:
      * kind='button_click', component_id='payment.cash_on_delivery'
      * kind='list_select', component_id='cruz-de-vida'
      * kind='flow_submit', component_id='shipping_details_v1'
      * kind='location_share'
      * kind='audio_received'
      * kind='order_cart_submit', component_id=catalog_id
    """
    return AnalyticsEvent(
        event_id=_new_id(),
        timestamp_ms=_now_ms(),
        category="wa_inbound",
        kind=kind,
        correlation={
            "session_id": session_id,
            "tenant_id": tenant_id,
            "wa_message_id": wa_message_id,
            "component_id": component_id,
        },
        payload={
            "component_title": component_title,
            **(payload_extra or {}),
        },
        tags=["interaction", kind],
    )


def make_outbound_sent(
    *,
    session_id: str,
    tenant_id: str | None,
    component_kind: str,  # "image" | "interactive.button" | "interactive.list" | ...
    wa_message_id: str | None,
    component_id: str | None = None,
    payload_extra: dict[str, Any] | None = None,
) -> AnalyticsEvent:
    """Outbound del agente. Sirve para emparejar luego con la respuesta del
    cliente y medir engagement (CTR per component, tiempo a respuesta, etc.)."""
    return AnalyticsEvent(
        event_id=_new_id(),
        timestamp_ms=_now_ms(),
        category="wa_outbound",
        kind=f"send.{component_kind}",
        correlation={
            "session_id": session_id,
            "tenant_id": tenant_id,
            "wa_message_id": wa_message_id,
            "component_id": component_id,
        },
        payload=payload_extra or {},
        tags=["outbound", component_kind],
    )


def make_delivery_status(
    *,
    session_id: str | None,
    tenant_id: str | None,
    wa_message_id: str,
    status: str,  # "sent" | "delivered" | "read" | "failed"
    pricing_type: str | None = None,
    category: str | None = None,
    billable: bool | None = None,
    cost_cents_usd: int | None = None,
) -> AnalyticsEvent:
    """Webhook `message_status` materializado de WhatsApp Cloud API.

    Emitido por `IngestDeliveryStatus` cada vez que Meta nos manda un update
    de delivery (sent/delivered/read/failed) sobre un outbound nuestro. Lleva
    el `pricing` que Meta resolvió + el `cost_cents_usd` ya computado contra
    el rate card local, para que dashboards downstream lo consuman directo
    sin re-cruzar pricing × rate card.

    `session_id` es `None` cuando el status no se pudo matchear a un
    outbound persistido (dead-letter); aún así emitimos el evento para que
    el dashboard registre el orphan.

    HU-WA24H-001 F1.10.
    """
    return AnalyticsEvent(
        event_id=_new_id(),
        timestamp_ms=_now_ms(),
        category="wa_outbound",
        kind="delivery_status",
        correlation={
            "session_id": session_id,
            "tenant_id": tenant_id,
            "wa_message_id": wa_message_id,
        },
        payload={
            "status": status,
            "pricing_type": pricing_type,
            "category": category,
            "billable": billable,
            "cost_cents_usd": cost_cents_usd,
        },
        tags=["outbound", "delivery_status", status],
    )


def make_conversion(
    *,
    session_id: str,
    tenant_id: str | None,
    event_name: str,  # Meta CAPI nombre: "Purchase", "AddToCart", "InitiateCheckout", "Lead"
    value: float | None = None,
    currency: str | None = None,
    reference_id: str | None = None,
    ctwa_clid: str | None = None,
    payload_extra: dict[str, Any] | None = None,
) -> AnalyticsEvent:
    """Evento de conversión — alineado con Meta Conversions API event_name.

    Los nombres canónicos: `Purchase`, `AddToCart`, `InitiateCheckout`,
    `Lead`, `ViewContent`, `Contact`. Los sinks lo mapean al payload CAPI.
    """
    return AnalyticsEvent(
        event_id=_new_id(),
        timestamp_ms=_now_ms(),
        category="conversion",
        kind=f"conversion.{event_name}",
        correlation={
            "session_id": session_id,
            "tenant_id": tenant_id,
            "reference_id": reference_id,
            "ctwa_clid": ctwa_clid,
        },
        payload={
            "event_name": event_name,
            "value": value,
            "currency": currency,
            **(payload_extra or {}),
        },
        tags=["conversion", event_name.lower()],
    )
