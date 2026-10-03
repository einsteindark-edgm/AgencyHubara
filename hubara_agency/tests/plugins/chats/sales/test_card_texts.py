"""Lo que el cliente lee dentro de cada tarjeta que redacta el LLM: una sola
lista para la verificación del turno y para el scorecard.

Caso 4567 del laboratorio (caso-fotos-0929-r3, turno 1 del bot nuevo): el
saludo con la marca iba en el texto de la lista, lo único que el cliente lee
con el menú. La verificación y la revisión solo leían los textos sueltos.
"""
from __future__ import annotations

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
