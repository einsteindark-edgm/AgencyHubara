"""Lo que el cliente lee dentro de cada tarjeta que redacta el LLM: una sola
lista para la verificación del turno y para el scorecard.

Caso 4567 del laboratorio (caso-fotos-0929-r3, turno 1 del bot nuevo): el
saludo con la marca iba en el texto de la lista, lo único que el cliente lee
con el menú. La verificación y la revisión solo leían los textos sueltos.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from src.plugins.chats.agent.sales.card_texts import CARD_TEXT_ARGS, card_texts
from src.plugins.chats.agent.sales.tools import ui_intents


def test_the_list_text_is_what_the_customer_reads_with_the_menu() -> None:
    args = {
        "handles": '["calabaza", "momia"]',
        "intro_text": "Buenos días, bienvenido a *Hubara*",
        "group_by": "categories",
    }

    assert card_texts("present_products", args) == ["Buenos días, bienvenido a *Hubara*"]


@pytest.mark.parametrize(
    "name, args, read",
    [
        ("send_quick_replies", {"body": "¿Cuál prefieres?", "buttons": ["Ver catálogo"]}, ["¿Cuál prefieres?"]),
        (
            "send_cta_url",
            {"url": "https://tienda.test/c", "button_text": "Ver", "body_text": "Aquí está el catálogo"},
            ["Aquí está el catálogo"],
        ),
        (
            "present_product_detail",
            {"handle": "calabaza", "caption_suffix": "aroma canela", "design": "Leo"},
            ["aroma canela"],
        ),
        (
            "present_variant_picker",
            {"variant_type": "color", "options": ["Lila"], "intro_text": "¿De qué color la quieres?"},
            ["¿De qué color la quieres?"],
        ),
        # El envío solo manda el contacto: el motivo no le llega al cliente.
        ("send_contact_card", {"reason": "para coordinar la entrega"}, []),
        ("search_products", {"q": "halloween"}, []),
        ("present_products", {"handles": ["calabaza"], "intro_text": "   "}, []),
        ("present_products", {"handles": ["calabaza"]}, []),
    ],
)
def test_each_card_gives_only_the_text_that_goes_with_it(name: str, args: dict[str, Any], read: list[str]) -> None:
    assert card_texts(name, args) == read


_FORM_MESSAGE = "Para enviarte tu pedido necesito unos datos 🤍\n\n• *2× Velón Koala* (Blanco · Lavanda)"


def test_the_check_of_the_turn_reads_the_message_the_code_wrote_with_the_form() -> None:
    """Incidente 2026-10-06 (turno 9): el formulario salió con su mensaje
    (producto, variantes, cantidad, subtotal) y la verificación ③ no lo veía:
    solo leía los textos que redacta el LLM. El texto viaja en el envelope de
    la tool (`customer_text`)."""
    from src.plugins.chats.agent.sales.decisions.facade import delivered_card_texts

    events = [
        {"name": "send_reply", "args": {"text": "Listo"}, "result": json.dumps({"reply": {"text": "Listo"}})},
        {
            "name": "request_shipping_details",
            "args": {"items": [{"handle": "velon-koala", "quantity": 2}]},
            "result": json.dumps({"queued": True, "kind": "shipping_flow", "customer_text": _FORM_MESSAGE}),
        },
        {
            "name": "send_quick_replies",
            "args": {"body": "¿Seguimos?"},
            "result": json.dumps({"queued": True, "kind": "quick_replies"}),
        },
    ]

    assert delivered_card_texts(events) == [_FORM_MESSAGE, "¿Seguimos?"]


def test_a_refused_card_or_an_unreadable_envelope_gives_no_text() -> None:
    from src.plugins.chats.agent.sales.decisions.facade import delivered_card_texts

    events = [
        {
            "name": "request_shipping_details",
            "args": {},
            "result": json.dumps({"queued": False, "error": "customer_deferred", "customer_text": _FORM_MESSAGE}),
        },
        {"name": "send_shipping_rates", "args": {}, "result": '{"queued": true, "customer_text": "Nues'},
        {"name": "present_order_confirmation", "args": {}, "result": json.dumps({"queued": True, "customer_text": "  "})},
    ]

    assert delivered_card_texts(events) == []


def test_envelope_card_text_reads_only_the_customer_text_of_the_envelope() -> None:
    from src.plugins.chats.agent.sales.card_texts import envelope_card_text

    assert envelope_card_text(json.dumps({"customer_text": f"  {_FORM_MESSAGE}\n"})) == _FORM_MESSAGE
    assert envelope_card_text(json.dumps({"summary": "Formulario enviado"})) is None
    assert envelope_card_text("texto plano") is None
    assert envelope_card_text(None) is None


def test_every_mapped_text_is_a_text_parameter_of_its_tool() -> None:
    """Un nombre que la tool no tiene no se cuela: DES-10 leía `caption` e
    `intro_text` de la ficha, que nunca existieron (es `caption_suffix`)."""
    tools = {
        cls.name: cls.parameters.get("properties") or {}
        for cls in vars(ui_intents).values()
        if isinstance(cls, type)
        and isinstance(getattr(cls, "name", None), str)
        and isinstance(getattr(cls, "parameters", None), dict)
    }

    assert CARD_TEXT_ARGS
    for name, keys in CARD_TEXT_ARGS.items():
        assert name in tools, name
        for key in keys:
            assert (tools[name].get(key) or {}).get("type") == "string", (name, key)
