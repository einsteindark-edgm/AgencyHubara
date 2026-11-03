"""Los hechos de la puerta de afirmaciones (2026-11-03): un builtin por hecho.

Colossus afirma cosas que nunca consultó (69 casos en 51 conversaciones desde
el 10-07; 25 la última semana). La capacidad de la puerta (`afirmacion_previa`,
antes de enviar) tiene la forma de `afirmacion`: Jev dice QUÉ afirma el texto
y la tabla del paquete decide con los HECHOS. Cada hecho es una vista
(`view: [ … ]`) que el código calcula desde lo que el turno y el sistema ya
saben; las frases que busca una vista son del paquete (su `with:`), nunca del
código:

  order_registered_fact         CON-03  hay un pedido registrado (este turno o antes, de esta compra)
  paid_in_turn_fact             POS-01  `check_order_status` dijo pagado en este turno
  unexplained_amounts_fact      DES-10  montos que no son del catálogo ni de lo que el turno consultó
  bank_details_fact             ENV-05  números de cuenta o llaves en el texto (la llave pública no)
  shipping_amounts_fact         ENV-05  montos dichos como valor del envío
  approved_delivery_time_fact   EST-05  el texto usa la frase aprobada de tiempos (domain.yaml)

Datos sintéticos: ningún texto ni número de un cliente real.
"""
from __future__ import annotations

import json

from src.plugins.chats.agent.sales.decisions.bundled_claims import (
    ClaimCheck,
    ClaimFacts,
    approved_delivery_time_fact,
    bank_details_fact,
    order_registered_fact,
    paid_in_turn_fact,
    shipping_amounts_fact,
    unexplained_amounts_fact,
)

POLICY = "envio, domicilio, contra entrega, minimo, desde, recargo, tarifa, faltan"
BANK = "nequi, daviplata, bancolombia, llave, numero de cuenta, cuenta de ahorros, cuenta corriente"
SHIPPING = "envio, domicilio, flete"
PUBLIC_KEY = "3009990000"


def _check(text: str = "", **kw) -> ClaimCheck:
    return ClaimCheck(text=text, **kw)


def _status(pay_status: str | None, confirmed: bool | None) -> dict:
    order: dict = {"order_id": "o_1", "status": "en preparación"}
    if pay_status is not None:
        order["pay_status"] = pay_status
    if confirmed is not None:
        order["payment_confirmed"] = confirmed
    return {"name": "check_order_status", "args": {}, "result": json.dumps({"orders": [order]})}


# ── CON-03: pedido registrado ───────────────────────────────────────────────


def test_an_order_registered_in_this_turn_counts() -> None:
    assert order_registered_fact(_check(turn_registered=True)) == {"order_registered": True}


def test_an_order_registered_earlier_in_this_purchase_counts() -> None:
    metadata = {
        "registered_order": {"success": True, "registered_at_ms": 2_000},
        "episodes": [{"episode_id": "ep_002", "started_at_ms": 1_000, "status": "active"}],
        "active_episode_id": "ep_002",
    }
    assert order_registered_fact(_check(facts=ClaimFacts(metadata=metadata))) == {"order_registered": True}


def test_without_a_registered_order_there_is_none() -> None:
    assert order_registered_fact(_check()) == {"order_registered": False}
    failed = {"registered_order": {"success": False}}
    assert order_registered_fact(_check(facts=ClaimFacts(metadata=failed))) == {"order_registered": False}


# ── POS-01: pago verificado en el turno ─────────────────────────────────────


def test_check_order_status_said_paid_in_this_turn() -> None:
    assert paid_in_turn_fact(_check(tool_events=(_status("paid", True),))) == {"paid_checked": True}


def test_a_pending_payment_or_no_check_is_not_paid() -> None:
    assert paid_in_turn_fact(_check(tool_events=(_status("pending", False),))) == {"paid_checked": False}
    assert paid_in_turn_fact(_check(tool_events=())) == {"paid_checked": False}
    # Otra tool que trae un `payment_confirmed` no es la verificación del pago.
    other = {"name": "search_products", "result": json.dumps({"orders": [{"payment_confirmed": True}]})}
    assert paid_in_turn_fact(_check(tool_events=(other,))) == {"paid_checked": False}


def test_an_unreadable_result_is_not_paid() -> None:
    broken = {"name": "check_order_status", "result": "{no es json"}
    assert paid_in_turn_fact(_check(tool_events=(broken,))) == {"paid_checked": False}


# ── DES-10: montos del catálogo o de lo consultado ──────────────────────────


def test_an_amount_the_catalog_does_not_explain_is_unexplained() -> None:
    inp = _check("La Vela Luna cuesta $23.000 🤍", facts=ClaimFacts(known_prices=(25_000, 18_000)))

    assert unexplained_amounts_fact(inp, politica=POLICY) == {"unexplained_amounts": ("$23.000",)}


def test_catalog_prices_quantities_and_policy_amounts_are_explained() -> None:
    """El caso legítimo: el precio del catálogo, por la cantidad, y el envío
    dicho como política no se retienen."""
    text = "La Vela Luna cuesta $25.000; las dos te quedan en $50.000. El envío en Bogotá cuesta $12.000."
    inp = _check(text, facts=ClaimFacts(known_prices=(25_000,), policy_amounts=(12_000,)))

    assert unexplained_amounts_fact(inp, politica=POLICY) == {"unexplained_amounts": ()}


def test_an_amount_that_a_tool_of_the_turn_returned_is_explained() -> None:
    """Lo que el turno consultó también cuenta (un total de la verificación)."""
    verify = {"name": "verify_order_for_checkout", "result": json.dumps({"ok": True, "total_cop": 61_500})}
    inp = _check("Tu total queda en $61.500 🤍", tool_events=(verify,), facts=ClaimFacts(known_prices=(25_000,)))

    assert unexplained_amounts_fact(inp, politica=POLICY) == {"unexplained_amounts": ()}


def test_the_text_the_model_wrote_never_explains_itself() -> None:
    """`send_reply` devuelve el texto que recibió: ese monto lo escribió el
    modelo, no lo consultó (lo que una tool repite de sus argumentos no cuenta)."""
    text = "La Vela Luna cuesta $23.000 🤍"
    reply = {"name": "send_reply", "args": {"text": text}, "result": json.dumps({"reply": {"text": text}})}
    inp = _check(text, tool_events=(reply,), facts=ClaimFacts(known_prices=(25_000,)))

    assert unexplained_amounts_fact(inp, politica=POLICY) == {"unexplained_amounts": ("$23.000",)}


def test_without_any_known_price_amounts_are_not_judged() -> None:
    inp = _check("Cuesta $23.000")

    assert unexplained_amounts_fact(inp, politica=POLICY) == {"unexplained_amounts": ()}


# ── ENV-05: datos bancarios ─────────────────────────────────────────────────


def test_an_account_number_in_the_text_is_bank_data() -> None:
    inp = _check("Puedes transferir a la cuenta de ahorros Bancolombia 123-456789-01 🤍")

    assert bank_details_fact(inp, habla_de_banco=BANK) == {"bank_details": ("123-456789-01",)}


def test_the_public_payment_key_of_the_store_is_allowed() -> None:
    """El caso legítimo: la llave Nequi del negocio es pública (ENV-05 la permite)."""
    inp = _check(f"Puedes pagar por Nequi al {PUBLIC_KEY} 🤍", facts=ClaimFacts(public_keys=(PUBLIC_KEY,)))

    assert bank_details_fact(inp, habla_de_banco=BANK) == {"bank_details": ()}


def test_naming_a_payment_method_or_a_price_is_not_bank_data() -> None:
    inp = _check("Puedes pagar por Nequi o Bancolombia. La vela cuesta $250.000 y te escribo al 3001112233 si hace falta.")

    assert bank_details_fact(inp, habla_de_banco=BANK) == {"bank_details": ()}


# ── ENV-05: valor del envío ─────────────────────────────────────────────────


def test_an_amount_said_as_the_shipping_value_is_found() -> None:
    inp = _check("Listo 🤍 El envío de tu pedido te sale en $14.500.")

    assert shipping_amounts_fact(inp, habla_de_envio=SHIPPING) == {"shipping_amounts": ("$14.500",)}


def test_a_price_without_shipping_words_is_not_a_shipping_value() -> None:
    inp = _check("La Vela Luna cuesta $25.000. ¿Te la envío en morado?")

    assert shipping_amounts_fact(inp, habla_de_envio="envio de tu pedido, domicilio, flete") == {"shipping_amounts": ()}


# ── EST-05: la frase aprobada de tiempos ────────────────────────────────────


def test_the_approved_delivery_phrase_is_not_a_promise() -> None:
    facts = ClaimFacts(delivery_phrases="2 a 3 dias habiles, 1 a 2 dias habiles")
    inp = _check("Los envíos llegan en 2 a 3 días hábiles después de despachar 🤍", facts=facts)

    assert approved_delivery_time_fact(inp) == {"approved_delivery_time": True}


def test_a_concrete_day_is_not_the_approved_phrase() -> None:
    facts = ClaimFacts(delivery_phrases="2 a 3 dias habiles")
    assert approved_delivery_time_fact(_check("¡Te llega mañana! 🤍", facts=facts)) == {"approved_delivery_time": False}
    # Sin frase aprobada en el dominio de la tienda, nada es la frase aprobada.
    assert approved_delivery_time_fact(_check("Llega en 2 a 3 días hábiles")) == {"approved_delivery_time": False}
