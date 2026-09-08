"""D1.9 — catálogo cerrado de eventos hacia Meta Business Agent (puro).

Los tipos son nuestros (Meta acepta cualquier ``type``): un vocabulario
chico y estable para que la skill de MBA sepa qué hacer con cada uno y para
que el dedupe (``type`` + ``order_id``) tenga sentido. ``description`` es la
instrucción que MBA lee: lleva el texto EXACTO que Hubara habría enviado
(la matriz determinista del ETA) y le pide transmitirlo sin inventar.
"""
from __future__ import annotations

__all__ = [
    "AGENT_EVENT_TYPES",
    "DESCRIPTION_MAX",
    "agent_event_type_for_stage",
    "build_description",
]

AGENT_EVENT_TYPES: tuple[str, ...] = (
    "payment_received",  # preparing con pago confirmado (Hubara concilió el pago)
    "order_preparing",   # preparing sin pago confirmado (contra entrega / pendiente)
    "order_ready",
    "order_shipped",
    "order_delivered",
    "order_cancelled",
    "episode_closed",    # D1.10: nota de frontera entre episodios
)

#: Tope prudente para ``description`` (Meta no publica el límite; 2000 es el
#: mismo que usa en ``metadata`` de thread_control).
DESCRIPTION_MAX = 2000

_STAGE_TYPES = {
    "ready": "order_ready",
    "shipping": "order_shipped",
    "delivered": "order_delivered",
    "cancelled": "order_cancelled",
}

_INSTRUCTION = (
    " — Transmítele esta novedad al cliente con tus palabras, en un solo mensaje breve. "
    "No inventes datos que no estén acá ni le pidas nada más."
)


def agent_event_type_for_stage(stage: str, *, payment_confirmed: bool) -> str | None:
    """Etapa del pedido (``src.platform.orders.state``) → tipo de evento.
    ``preparing`` distingue si el pago está realmente confirmado
    (``pay_status == paid``, no la modalidad)."""
    if stage == "preparing":
        return "payment_received" if payment_confirmed else "order_preparing"
    return _STAGE_TYPES.get(stage)


def build_description(event_type: str, message: str) -> str:
    """Instrucción para MBA: la novedad tal cual Hubara la habría dicho + la
    consigna de transmitirla sin inventar. Acotada a ``DESCRIPTION_MAX``."""
    budget = DESCRIPTION_MAX - len(_INSTRUCTION)
    return f"{message.strip()[:budget]}{_INSTRUCTION}"
