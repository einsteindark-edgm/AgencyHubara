"""Lo que lee el cliente en la invitación a calificar (puro).

Decisión del operador (2026-10-13): la invitación dice el descuento «para que
la gente la abra». Fuera de la ventana va como plantilla de MARKETING
(`order_review_marketing_v1`, aprobada en Meta con este mismo marco); dentro,
el mismo texto como mensaje normal con el formulario. Tuteo, sin voseo. La
plantilla no saluda por nombre (un parámetro vacío la tumba con 131008).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

#: Plantilla de marketing (fuera de la ventana): foto + texto + botón FLOW.
TEMPLATE_NAME = "order_review_marketing_v1"
#: Texto del botón que abre el formulario (≤ 20 en mensajes interactivos).
FLOW_CTA = "Calificar mi pedido"
#: La pantalla del Flow `review_v1` que abre el botón.
FLOW_SCREEN = "RATING"

_REWARD = "5 % de descuento en tu próxima compra (10 % si nos compartes una foto)"
_REWARD_NOTE = (
    "Al enviar te guardamos 5 % para tu próxima compra "
    "(10 % si compartes una foto y nos dejas publicarla)."
)
_MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)
#: Colombia no tiene horario de verano: UTC−5 todo el año.
_BOGOTA = timezone(timedelta(hours=-5))


def items_summary(titles: list[str]) -> str:
    """«Velón A» o «Velón A y 2 más»."""
    clean = [t.strip() for t in titles if t and t.strip()]
    if not clean:
        return "Tu pedido"
    if len(clean) == 1:
        return clean[0]
    return f"{clean[0]} y {len(clean) - 1} más"


def invitation_body(order_reference: str) -> str:
    """El cuerpo de la invitación (el de la plantilla, con `{{1}}` lleno)."""
    return (
        f"¡Tu pedido {order_reference} ya llegó! 🕯️\n\n"
        f"Cuéntanos qué tal te pareció: calificarlo toma 10 segundos y te "
        f"regalamos {_REWARD}."
    )


def template_variables(order_reference: str) -> dict[str, str]:
    return {"order_reference": order_reference}


def flow_action_data(order_reference: str, titles: list[str]) -> dict[str, Any]:
    """Los datos de la primera pantalla del formulario (endpoint-less)."""
    return {
        "order_label": f"Pedido {order_reference}",
        "items_summary": items_summary(titles),
        "reward_note": _REWARD_NOTE,
    }


def expires_label(ms: int) -> str:
    """«12 de diciembre» en hora de Bogotá."""
    day = datetime.fromtimestamp(ms / 1000, tz=_BOGOTA)
    return f"{day.day} de {_MONTHS[day.month - 1]}"
