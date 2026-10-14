"""Las reglas de los hechos: qué le proponen a Jev de pago, estado del pedido,
cupón y negación (incidente 2026-10-13), con las frases que trae `ventas-7`.

Estas reglas son de palabras: solo PROPONEN (las capacidades del paquete no
actúan sin Jev). Igual conviene que propongan poco: cada candidato falso es
una pregunta más a Jev. Los casos que NO deben proponerse son los de la
revisión del 2026-10-13 (frases de venta ciertas que la primera versión
marcaba). Las frases viven en el paquete: estos casos prueban las de hoy.
"""
from __future__ import annotations

import pytest

from src.plugins.chats.agent.sales.decisions import registry
from src.plugins.chats.agent.sales.use_cases.fact_rules import (
    CouponWords,
    DenialWords,
    PaymentWords,
    StatusWords,
    coupon_claims,
    denial_claims,
    payment_claims,
    phrases,
    status_claims,
    talks_about_coupons,
    talks_about_order,
)

PENDING = dict(order="#67", stage="preparing", pay_status="pending")


@pytest.fixture(autouse=True)
def _bundle(monkeypatch):
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-7")
    registry.reset()
    yield
    registry.reset()


def _words(capability: str, cls: type):
    return cls(**registry.capability(capability).items_params)


def _status(text: str) -> list[str]:
    return [term for term, _fact in status_claims(text, **PENDING, words=_words("estado_ajeno", StatusWords))]


@pytest.mark.parametrize(
    "text",
    [
        "¡Listo! Tu pago quedó confirmado 🤍",
        "Ya recibimos tu pago, ¡gracias!",
        "Tu pedido ya va en camino 🚚",
        "Tu pedido ya fue despachado.",
        "Tu pedido ya fue entregado.",
        # Premortem del PR #426: las formas naturales que se escapaban.
        "¡Listo! El pago quedó confirmado ✅",
        "Pago confirmado ✅",
        "Ya confirmamos el pago de tu pedido.",
        "Ya recibimos el pago, ¡gracias!",
        "Ya despachamos tu pedido 🚚",
        "Ya enviamos tu pedido.",
        "Tu pedido salió hoy.",
        "Ya entregamos tu pedido.",
        # Lo que viene DESPUÉS («y te avisamos cuando…») no lo vuelve futuro.
        "Tu pago quedó confirmado ✅ y te avisamos cuando despachemos",
        "Tu pedido ya va en camino 🚚 y te comparto la guía cuando la tenga",
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
        "Tu pago no está confirmado todavía.",           # el «…» de una frase no se salta un «no»
        "Te confirmo el pago apenas lo vea en la cuenta.",
    ],
)
def test_what_is_promised_listed_or_about_something_else_is_not_proposed(text: str) -> None:
    assert _status(text) == []


def _payment(text: str, subtotal: int | None = 25_000) -> list[str]:
    return [t for t, _f in payment_claims(
        text, subtotal=subtotal, cod_min=45_000, surcharges=("1,5 %", "2,69 %"), words=_words("pago_ajeno", PaymentWords)
    )]


@pytest.mark.parametrize(
    "text",
    [
        "Claro, puedes pagar contra entrega 🤍",
        "Con el link hay un recargo del 3 %.",
        "El link de pago no tiene recargo.",
        "Con el link no hay recargo 🤍",
        "Puedes pagar en efectivo al recibir.",
    ],
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
        # La condición entre paréntesis es de su cláusula (premortem del PR #426).
        "Contra entrega (compras desde $45.000) o link de pago 🤍",
    ],
)
def test_the_general_rule_a_condition_or_another_method_is_not_proposed(text: str) -> None:
    assert _payment(text) == []


def _coupon(text: str, known: tuple[str, ...] = ("AMOR2026", "HALLOWEEN", "BIENVENIDA"),
            valid: tuple[str, ...] = ()) -> list[str]:
    return [t for t, _f in coupon_claims(
        text, known=known, valid=valid, valid_lines=(), valid_percents=(), words=_words("cupon_ajeno", CouponWords)
    )]


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
        text, titles=("Plegaria de Luz", "Velas de deseos cuadradas"), own=("Chanel",), products=("Plegaria de Luz",),
        words=_words("negacion_falsa", DenialWords),
    )]


def test_denying_a_product_the_catalog_has_is_proposed() -> None:
    assert _denial("Lo siento, no tenemos la Plegaria de Luz.") == ["Plegaria de Luz"]


@pytest.mark.parametrize(
    "text",
    [
        "No hay problema si prefieres la Plegaria de Luz.",
        "No hay afán: la Plegaria de Luz te llega mañana.",
        "No hay ningún problema con la Plegaria de Luz.",
    ],
)
def test_an_idiom_with_no_is_not_a_denial(text: str) -> None:
    assert _denial(text) == []


def test_a_denial_of_how_it_comes_counts_only_for_its_options() -> None:
    assert _denial("La Plegaria de Luz no viene en Chanel.") == ["Chanel"]


# ── Cómo se lee una frase del paquete ──────────────────────────────────────


@pytest.mark.parametrize(
    ("spec", "text", "found"),
    [
        ("despachad*", "ya fue despachada", True),
        ("despachad*", "despacho", False),
        ("tu/su pago … confirmad*", "su pago ya quedo confirmado", True),
        ("tu/su pago … confirmad*", "tu pago de la compra de ayer fue confirmado", False),  # más de tres en medio
        ("mínimo", "el minimo es", True),
        ("link", "linkear", False),
        ("si añades", "si anades otra", True),
        ("", "cualquier cosa", False),
    ],
)
def test_a_phrase_of_the_bundle_reads_whole_words(spec: str, text: str, found: bool) -> None:
    import re

    assert bool(re.search(phrases(spec), text)) is found


@pytest.mark.parametrize("spec", ["(contra|entrega)", "… pago", "pago …", "tu\\s+pago"])
def test_a_phrase_is_never_a_regex(spec: str) -> None:
    with pytest.raises(ValueError):
        phrases(spec)


def test_the_order_is_read_only_when_a_pending_order_would_be_contradicted() -> None:
    words = _words("estado_ajeno", StatusWords)

    assert talks_about_order("¡Listo! Tu pago quedó confirmado 🤍", words)
    assert not talks_about_order("Apenas recibamos tu pago te confirmamos.", words)
    assert not talks_about_order("¿A qué ciudad te las envío?", words)


@pytest.mark.parametrize(
    ("text", "read"),
    [
        ("Te aplico el cupón 🤍", True),
        ("Hoy hay 10 % de descuento.", True),
        ("Usa HALLOWEEN al pagar.", True),                    # escrito como código, sin «cupón»
        ("Con el link el recargo es de 1,5 %.", False),       # un porcentaje que no es descuento
        ("¿A qué ciudad te las envío? Somos de BOG", False),  # tres letras no son un código
    ],
)
def test_the_coupons_are_read_only_when_the_text_could_name_one(text: str, read: bool) -> None:
    assert talks_about_coupons(text, _words("cupon_ajeno", CouponWords)) is read
