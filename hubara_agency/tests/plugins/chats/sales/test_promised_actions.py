"""Lo que el texto del bot promete hacer AHORA, y si lo hizo (incidente del
2026-10-09: «Te paso el formulario para los datos de envío» dos turnos
seguidos sin llamar `request_shipping_details`).

`broken_promises(texto, tools_usadas)` es puro (lo lee la segunda puerta del
turno en el workflow V2, la red de la activity y la calificación): por cada
componente que el texto promete y ninguna de sus tools se usó, una promesa
rota con la nota que nombra la tool. Una oferta («si quieres te envío las
fotos»), una pregunta («¿te paso el formulario?»), un condicional («cuando me
confirmes te paso el resumen») o un pasado («ya te envié el formulario») no
son promesas de ahora.
"""
from __future__ import annotations

import pytest

from src.plugins.chats.agent.sales.use_cases.promised_actions import broken_promises, promised_kinds


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Perfecto, contra entrega.\n\nTe paso el formulario para los datos de envío 🤍", "formulario"),
        ("Te envío el formulario y dejamos el pedido listo", "formulario"),
        ("Ahí te va el formulario 🤍", "formulario"),
        ("Te comparto las tarifas de envío.", "tarifas"),
        ("Ya mismo te paso los costos de envío 🚚", "tarifas"),
        ("Listo, te paso el resumen de tu pedido para que lo confirmes.", "resumen"),
        ("¡Claro! Te muestro nuestro catálogo 🤍", "catalogo"),
        ("Te envío las fotos del Velón Koala.", "fotos"),
        ("Te muestro cómo se ve en lila, te mando una foto.", "fotos"),
        ("Te muestro los aromas disponibles para que elijas.", "opciones"),
        ("Te paso los colores que tenemos de esa vela.", "opciones"),
        ("¡Listo! Tu pedido quedó registrado 🤍", "registro"),
        ("Tu compra ya está confirmada, gracias.", "registro"),
    ],
)
def test_each_promise_is_read_with_its_component(text: str, kind: str) -> None:
    assert kind in promised_kinds(text)


@pytest.mark.parametrize(
    "text",
    [
        "¿Te paso el formulario para los datos de envío?",
        "Si quieres te envío las fotos del Velón Koala.",
        "Si gustas te muestro el catálogo.",
        "Cuando me confirmes el color, te paso el resumen.",
        "Apenas elijas el aroma te paso el formulario.",
        "Ya te envié el formulario, cuando lo llenes seguimos 🤍",
        "El formulario te pide ciudad, dirección y teléfono.",
        "No te puedo enviar fotos de ese diseño, no lo manejamos.",
        "La Calabaza viene en naranja y huele a canela.",
        "El envío a Bogotá llega en 1 a 2 días hábiles.",
    ],
)
def test_offers_questions_conditionals_and_facts_are_not_promises(text: str) -> None:
    assert promised_kinds(text) == set()


def test_a_promise_kept_in_the_same_turn_is_not_broken() -> None:
    assert broken_promises("Te paso el formulario 🤍", ["send_reply", "request_shipping_details"]) == []
    assert broken_promises("Te muestro nuestro catálogo", ["list_categories"]) == []
    assert broken_promises("Te envío las fotos", ["present_product_gallery"]) == []


def test_a_broken_promise_names_the_tool_that_keeps_it() -> None:
    (promise,) = broken_promises("Perfecto. Te paso el formulario para los datos de envío 🤍", ["send_reply"])

    assert promise.kind == "formulario"
    assert "request_shipping_details" in promise.nudge


def test_every_broken_promise_of_the_text_is_reported_once() -> None:
    text = "Te comparto las tarifas de envío y te paso el formulario. Te paso el formulario 🤍"

    kinds = [p.kind for p in broken_promises(text, ["send_reply"])]

    assert sorted(kinds) == ["formulario", "tarifas"]


def test_claiming_a_registered_order_without_registering_it_is_broken() -> None:
    (promise,) = broken_promises("¡Listo! Tu pedido quedó registrado 🤍", ["send_reply"])

    assert promise.kind == "registro" and "register_order" in promise.nudge
    assert broken_promises("¡Listo! Tu pedido quedó registrado 🤍", ["register_order", "send_reply"]) == []
