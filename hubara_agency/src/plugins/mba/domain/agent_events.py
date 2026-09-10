"""D1.9 — catálogo cerrado de eventos hacia Meta Business Agent (puro).

Los tipos son nuestros (Meta acepta cualquier ``type``): un vocabulario
chico y estable para que la skill de MBA sepa qué hacer con cada uno y para
que el dedupe (``type`` + ``order_id`` / ``episode_id``) tenga sentido.
``description`` es la instrucción que MBA lee: lleva el texto EXACTO que
Hubara habría enviado (la matriz determinista del ETA) y le pide
transmitirlo sin inventar; para ``episode_closed`` (D1.10) lleva la nota de
frontera y le pide callar.
"""
from __future__ import annotations

__all__ = [
    "AGENT_EVENT_TYPES",
    "DESCRIPTION_MAX",
    "agent_event_type_for_stage",
    "build_description",
    "episode_closed_message",
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
#: D1.10 — nota de FRONTERA entre episodios: MBA no tiene API para resetear
#: el contexto del hilo; este evento le dice que lo anterior quedó cerrado.
#: Silencioso para el cliente (verificar en F0 que un agent_event puede serlo).
_BOUNDARY_INSTRUCTION = " — No le escribas al cliente por esto ni lo menciones."
_INSTRUCTION_BY_TYPE = {"episode_closed": _BOUNDARY_INSTRUCTION}

_CLOSING_LABELS = {
    "COMPRA_EXITOSA": "compra completada",
    "CONFIRMADO_PAGO_PENDIENTE": "pedido registrado, pago pendiente de verificación por el equipo",
    "CONFIRMADO_SIN_DATOS": "pasó a un colega para completar el pedido",
    "RECHAZO": "el cliente no compró",
}
#: Cierres con pedido en manos del equipo: el hilo sigue en MBA hasta que un
#: humano escriba, así que MBA NO debe arrancar una venta nueva ni retomar el
#: pedido — deriva al colega (revisión D1.10, M-1).
_CLOSED_WITH_ORDER = frozenset({"COMPRA_EXITOSA", "CONFIRMADO_PAGO_PENDIENTE", "CONFIRMADO_SIN_DATOS"})
_GUIDANCE_WITH_ORDER = (
    "Un colega del equipo gestiona ese pedido: si el cliente escribe sobre él (comprobante, dudas, cambios), "
    "dile que un colega le confirma en breve; no lo retomes ni inicies una venta nueva."
)
_GUIDANCE_FRESH_START = (
    "Si vuelve a escribir, es una conversación nueva: salúdalo, no retomes el pedido anterior ni reutilices "
    "sus datos de envío; empieza el guion desde el principio."
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
    consigna del tipo (transmitirla sin inventar; para ``episode_closed``,
    callar). Acotada a ``DESCRIPTION_MAX``."""
    instruction = _INSTRUCTION_BY_TYPE.get(event_type, _INSTRUCTION)
    budget = DESCRIPTION_MAX - len(instruction)
    return f"{message.strip()[:budget]}{instruction}"


def episode_closed_message(closing_tag: str, *, order_reference: str | None = None) -> str:
    """Texto de la nota de frontera: qué pasó con la conversación anterior y
    qué hacer si el cliente vuelve (según el cierre: con pedido en manos del
    equipo → derivar al colega; sin pedido → conversación nueva)."""
    tag = str(closing_tag or "").upper()
    label = _CLOSING_LABELS.get(tag, str(closing_tag or "cerrada").lower())
    ref = f", pedido {order_reference}" if order_reference else ""
    guidance = _GUIDANCE_WITH_ORDER if tag in _CLOSED_WITH_ORDER else _GUIDANCE_FRESH_START
    return f"La conversación anterior con este cliente quedó cerrada ({label}{ref}). {guidance}"
