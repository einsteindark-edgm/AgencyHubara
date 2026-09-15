"""Trayectorias de incidentes reales, reconstruidas como fixtures del scorecard.

Regla del plan (§6): ningún incidente cierra sin su check, y cada check nace
con la trayectoria del incidente como test. Sin teléfonos reales: solo run ids.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from tests.evals.scorecard.dsl import T, tool, traj

AROMAS = (
    "Caballero de la noche", "Limoncillo", "Lavanda", "Café", "Sándalo", "Ylang ylang",
    "Coco cremoso", "Frutos rojos", "Verde menta", "Drakar", "Chanel",
)
COLORS = ("Azul", "Rosado", "Blanco", "Negro", "Verde", "Rojo")

CATALOG_CTX = CheckContext(
    aromas=AROMAS,
    colors=COLORS,
    product_titles=("Cubo Love", "Vela Ángel", "Duo Zodiacal"),
    catalog_available=True,
)

_ENUMERATION = (
    "Tenemos 11 aromas disponibles: Caballero de la noche, Limoncillo, Lavanda, Café, "
    "Sándalo, Ylang ylang, Coco cremoso, Frutos rojos, Verde menta, Drakar y Chanel."
)
_DRAFT_CHOSEN = {"producto": "cubo-love", "color": "Azul"}
_DRAFT_VARIANTS = {"producto": "cubo-love", "color": "Azul", "aroma": "Café", "cantidad": "1"}
_GREETING = (
    "¡Buenos días! Bienvenido a *Hubara*, velas artesanales hechas a base de cera de "
    "palma, a mano en Colombia."
)
_HANDOFF = (
    "Usuario respondió: Voy apenas en camino a casa. Siguiente paso: confirmar el "
    "pedido y tomar datos de envío."
)


def pr281_before_fix():
    """Runs 01a0a0eb (remarketing) / 01a0a0f1 (sales), 2026-09-14, antes del PR #281.

    El evaluador legado le dio 0.93. Esperado: FALLA con 5 críticos.
    """
    return traj(
        T(1, inbound="Hola, quiero información de las velas", first_contact=True,
          sent=[_GREETING], tools=[tool("send_quick_replies", body="¿Te muestro el catálogo?")]),
        T(2, inbound="es para un regalo", sent=["¿Qué aroma le gusta a la persona?"]),
        T(3, inbound="café", tools=[tool("search_products", notes=["count:23"], q="café")],
          sent=["¿Para qué espacio sería? ¿La sala o el dormitorio?"]),
        T(4, inbound="la sala", sent=["¿Prefieres algún color?"]),
        T(5, inbound="muéstrame", llm=_ENUMERATION, sent=[_ENUMERATION],
          tools=[tool("search_products", notes=["count:24"]), tool("present_products")]),
        T(6, inbound="El primero azulito", stage_out="variantes", draft=_DRAFT_CHOSEN,
          tools=[tool("set_order_slot", producto="cubo-love", color="Azul")],
          sent=["¿Te lo dejo en azul?"]),
        T(7, inbound="Tengo cita médica, luego te escribo", signal="deferral",
          stage_in="variantes", draft=_DRAFT_CHOSEN, sent=["Claro, aquí te espero 🤍"]),
        T(8, trigger="ghost", inbound="[SISTEMA]: El usuario dejó de responder", stage_in="variantes",
          draft=_DRAFT_CHOSEN, tools=[tool("manage_conversation_tag", tag="INTERESADO")],
          state={"tag": "INTERESADO", "route": "ventas", "changes": [{"tag": "INTERESADO", "source": "llm", "reason": None}]}),
        T(9, trigger="handoff", inbound=_HANDOFF, signal="deferral", stage_in="variantes",
          stage_out="confirmacion", draft=_DRAFT_VARIANTS, at_ms=20_000_000,
          narration=["Perfecto, ya casi llegas a casa. Te dejo el formulario."],
          tools=[tool("set_order_slot", aroma="Café", cantidad="1"),
                 tool("request_shipping_details", order_total_cop=89000)]),
        T(10, trigger="ghost", inbound="[SISTEMA]: El usuario dejó de responder", stage_in="confirmacion",
          draft=_DRAFT_VARIANTS, at_ms=20_300_000,
          tools=[tool("manage_conversation_tag", tag="CONFIRMADO_SIN_DATOS"),
                 tool("escalate_to_human", reason_category="ORDER_PENDING_SHIPPING_DETAILS")],
          state={"tag": "HUMANO", "route": "humano", "escalation_reason": "ORDER_PENDING_SHIPPING_DETAILS",
                 "closing_tag": "CONFIRMADO_SIN_DATOS",
                 "changes": [{"tag": "CONFIRMADO_SIN_DATOS", "source": "llm", "reason": None},
                             {"tag": "HUMANO", "source": "llm", "reason": "ORDER_PENDING_SHIPPING_DETAILS"}]}),
        closing_tag="CONFIRMADO_SIN_DATOS",
    )


def pr281_after_fix():
    """El mismo episodio re-jugado con las guardas del PR #281.

    El cliente se salva, pero el LLM intenta lo mismo. Esperado: ALERTA.
    """
    base = pr281_before_fix()
    turns = list(base.turns)
    turns[4] = T(5, inbound="muéstrame", llm=_ENUMERATION, sent=[], suppressed="variant_enumeration_guard",
                 guards=["variant_enumeration_guard"],
                 tools=[tool("search_products", notes=["count:24"]), tool("present_products")])
    turns[8] = T(9, trigger="handoff", inbound=_HANDOFF, signal="deferral", stage_in="variantes",
                 stage_out="confirmacion", draft=_DRAFT_VARIANTS, at_ms=20_000_000,
                 narration=["Perfecto, ya casi llegas a casa. Te dejo el formulario."],
                 sent=["Perfecto, cuando llegues me escribes y seguimos 🤍"],
                 tools=[tool("set_order_slot", aroma="Café", cantidad="1"),
                        tool("request_shipping_details", ok=False, error="customer_deferred")])
    turns[9] = T(10, trigger="ghost", inbound="[SISTEMA]: El usuario dejó de responder", stage_in="confirmacion",
                 draft=_DRAFT_VARIANTS, at_ms=20_300_000,
                 tools=[tool("manage_conversation_tag", notes=["degraded_from:CONFIRMADO_SIN_DATOS"], tag="CONFIRMADO_SIN_DATOS"),
                        tool("escalate_to_human", ok=False, error="purchase_not_confirmed",
                             reason_category="ORDER_PENDING_SHIPPING_DETAILS")],
                 state={"tag": "INTERESADO", "route": "ventas",
                        "changes": [{"tag": "INTERESADO", "source": "llm", "reason": None}]})
    return traj(*turns, closing_tag=None)
