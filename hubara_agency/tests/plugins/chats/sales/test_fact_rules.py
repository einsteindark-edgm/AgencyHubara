"""Las reglas de los hechos (puro): qué le proponen a Jev de pago, estado del
pedido, cupón y negación (incidente 2026-10-13).

Estas reglas son de palabras: solo PROPONEN (las capacidades del paquete no
actúan sin Jev). Igual conviene que propongan poco: cada candidato falso es
una pregunta más a Jev. Los casos que NO deben proponerse son los de la
revisión del 2026-10-13 (frases de venta ciertas que la primera versión
marcaba).
"""
from __future__ import annotations

import pytest

from src.plugins.chats.agent.sales.use_cases.fact_rules import (
    coupon_claims,
    denial_claims,
    payment_claims,
    status_claims,
)

PENDING = dict(order="#67", stage="preparing", pay_status="pending")


def _status(text: str) -> list[str]:
    return [term for term, _fact in status_claims(text, **PENDING)]


@pytest.mark.parametrize(
    "text",
    [
        "¡Listo! Tu pago quedó confirmado 🤍",
        "Ya recibimos tu pago, ¡gracias!",
        "Tu pedido ya va en camino 🚚",
        "Tu pedido ya fue despachado.",
        "Tu pedido ya fue entregado.",
    ],
)
def test_a_status_given_as_a_fact_is_proposed(text: str) -> None:
    assert _status(text)


@pytest.mark.parametrize(
    "text",
    [
        "Tu pedido será entregado entre 2 y 4 días hábiles.",
        "Una vez despachado, te comparto la guía.",
        "Apenas recibamos tu pago te confirmamos.",
        "Recibimos el pago por Nequi, Daviplata o transferencia.",
        "Te he enviado el link de pago.",
        "¿Ya te llegó el formulario?",
        "Te avisamos apenas esté en camino 🤍",
    ],
)
def test_what_is_promised_listed_or_about_something_else_is_not_proposed(text: str) -> None:
    assert _status(text) == []


def _payment(text: str, subtotal: int | None = 25_000) -> list[str]:
    return [t for t, _f in payment_claims(text, subtotal=subtotal, cod_min=45_000, surcharges=("1,5 %", "2,69 %"))]


@pytest.mark.parametrize(
    "text",
    ["Claro, puedes pagar contra entrega 🤍", "Con el link hay un recargo del 3 %.", "El link de pago no tiene recargo."],
)
def test_a_payment_offer_the_order_does_not_have_is_proposed(text: str) -> None:
    assert _payment(text)


@pytest.mark.parametrize(
    "text",
    [
        "Contra entrega aplica para pedidos de $45.000 en adelante.",
        "Para contra entrega el pedido debe sumar al menos $45.000.",
        "Si agregas otra vela ya puedes pagar contra entrega.",
        "Por Nequi es transferencia directa, sin recargo.",
        "Puedes pagar con tarjeta en 3 cuotas al 0 %.",
        "Con el link el recargo es de 1,5 % con Nequi y 2,69 % con otros bancos.",
    ],
)
def test_the_general_rule_a_condition_or_another_method_is_not_proposed(text: str) -> None:
    assert _payment(text) == []


def _coupon(text: str, known: tuple[str, ...] = ("AMOR2026", "HALLOWEEN", "BIENVENIDA"),
            valid: tuple[str, ...] = ()) -> list[str]:
    return [t for t, _f in coupon_claims(text, known=known, valid=valid, valid_lines=(), valid_percents=())]


@pytest.mark.parametrize(
    "text",
    ["Te aplico el cupón AMOR2026 🤍", "Con el código amor2026 tienes descuento.", "Usa HALLOWEEN al pagar."],
)
def test_a_coupon_code_that_is_not_valid_is_proposed(text: str) -> None:
    assert _coupon(text)


@pytest.mark.parametrize(
    "text",
    [
        "Las velas de Halloween están en promoción 🎃",
        "¡Te damos la bienvenida! Hoy tenemos promo en velones.",
    ],
)
def test_a_code_that_is_also_a_common_word_needs_to_be_written_as_a_code(text: str) -> None:
    assert [t for t in _coupon(text) if "%" not in t] == []


def _denial(text: str) -> list[str]:
    return [t for t, _f in denial_claims(
        text, titles=("Plegaria de Luz", "Velas de deseos cuadradas"), own=("Chanel",), products=("Plegaria de Luz",)
    )]


def test_denying_a_product_the_catalog_has_is_proposed() -> None:
    assert _denial("Lo siento, no tenemos la Plegaria de Luz.") == ["Plegaria de Luz"]


@pytest.mark.parametrize(
    "text",
    ["No hay problema si prefieres la Plegaria de Luz.", "No hay afán: la Plegaria de Luz te llega mañana."],
)
def test_an_idiom_with_no_is_not_a_denial(text: str) -> None:
    assert _denial(text) == []
