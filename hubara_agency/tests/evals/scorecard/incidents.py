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


def traces_from(t) -> list[dict]:
    """Trayectoria del DSL → registros de traza como los escribe el worker."""
    out = []
    for turn in t.turns:
        out.append({
            "v": 1, "session_id": t.session_id, "episode_id": t.episode_id, "turn": turn.turn,
            "recorded_at_ms": (turn.at_ms or 0) + 5_000, "turn_started_ms": turn.at_ms,
            "trigger": turn.trigger, "inbound_text": turn.inbound_text, "first_contact": turn.first_contact,
            "tools": [{"name": c.name, "ok": c.ok, "error": c.error, "notes": list(c.notes), "args": dict(c.args)}
                      for c in turn.tools],
            "discarded_narration": list(turn.discarded_narration), "llm_text": turn.llm_text,
            "sent_texts": list(turn.sent_texts), "suppressed_reason": turn.suppressed_reason,
            "guards": list(turn.guards), "stage_in": turn.stage_in, "stage_out": turn.stage_out,
            "draft": dict(turn.draft or {}), "confirmed": turn.confirmed, "confirmed_by": None,
            "signal": {"kind": turn.signal, "text": turn.inbound_text} if turn.signal else None,
            "state": dict(turn.state),
        })
    return out


# ── Run ebbc203d (2026-09-16): precio del anuncio en vez del catálogo ───────
_HALLOWEEN_CTX = CheckContext(
    aromas=("Frutos rojos",),
    colors=("Blanco", "Naranja"),
    product_titles=("Trilogía del Terror", "Calabaza", "Momia", "Fantasma"),
    catalog_available=True,
    catalog_prices=(49500, 16000, 17500, 19000),
)
_AD_PRICE_QUOTE = (
    "El set de la Trilogía del Terror tiene un valor de *$45.000 COP*.\n\n"
    "Sobre el pago: sí manejamos contra entrega, y aplica justo desde $45.000 en productos, "
    "así que tu pedido califica. El valor del envío lo confirma la transportadora al despachar.\n\n"
    "¿Lo dejamos así?"
)
_CATALOG_PRICE_QUOTE = (
    "El set de la Trilogía del Terror tiene un valor de *$49.500 COP*.\n\n"
    "Sobre el pago: sí manejamos contra entrega, y aplica desde $45.000 en productos, "
    "así que tu pedido califica. El valor del envío lo confirma la transportadora al despachar.\n\n"
    "¿Lo dejamos así?"
)
_TRILOGIA_DRAFT = {"producto": "Trilogía del Terror", "cantidad": "1"}


def _ebbc203d(price_quote: str):
    return traj(
        T(1, inbound="¡Hola! Quiero más información sobre la colección de Haloween", first_contact=True,
          sent=[_GREETING.replace("Buenos días", "Buenas tardes")],
          tools=[tool("search_products", notes=["count:4"], q="halloween"), tool("present_products")]),
        T(2, inbound="Tienes envíos a Cali",
          sent=["Sí, hacemos envíos a todo Colombia, Cali incluida. El valor del envío se confirma al despachar con la transportadora."]),
        T(3, inbound="Quisiera la trilogía de terror", stage_out="variantes", draft={"producto": "Trilogía del Terror"},
          tools=[tool("set_order_slot", producto="Trilogía del Terror"), tool("get_product_by_handle", handle="trilogia-del-terror")],
          sent=["El set viene en aroma de frutos rojos y color blanco, así que no hay variantes por elegir. ¿Cuántas unidades quieres?"]),
        T(4, inbound="Solo uno", stage_in="variantes", draft=_TRILOGIA_DRAFT,
          tools=[tool("set_order_slot", cantidad="1"),
                 tool("request_shipping_details", ok=False, error="purchase_not_confirmed")],
          sent=["Listo, un set de la Trilogía del Terror."]),
        T(5, inbound="Tienes pago contra entrega", stage_in="confirmacion", draft=_TRILOGIA_DRAFT,
          tools=[tool("send_quick_replies", body=price_quote)]),
        closing_tag="INTERESADO",
    )


def ebbc203d_before_fix():
    """El bot citó "$45.000" (precio del anuncio) con el set a $49.500 en catálogo."""
    return _ebbc203d(_AD_PRICE_QUOTE)


def ebbc203d_after_fix():
    """Mismo episodio citando el precio del catálogo: el check no dispara."""
    return _ebbc203d(_CATALOG_PRICE_QUOTE)


def halloween_ctx() -> CheckContext:
    return _HALLOWEEN_CTX
