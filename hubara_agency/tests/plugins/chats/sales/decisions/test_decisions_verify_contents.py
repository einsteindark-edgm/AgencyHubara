"""③ ve QUÉ muestra cada tarjeta, no solo su nombre (ventas-7, 2026-10-13).

Conversación del anuncio de velitas (16:53 Bogotá): el cliente pidió «más
información sobre las velitas del 7 de diciembre» y el bot le mandó botones;
después, el catálogo completo de 30. La verificación leía el texto de la lista
y la palabra `present_products`, nunca qué productos llevaba ni qué decían
los botones. Sonda con Jev real (pregunta precisa del catálogo): la lista con
las dos velitas daba 0,58 sin ver su contenido y 0,82 viéndolo; el catálogo
completo, 0,36.

`card_contents` describe lo que la tarjeta MUESTRA (los productos de la lista,
la ficha, las etiquetas de los botones). Es aparte de `card_texts` (lo que el
cliente LEE con la tarjeta), que también usa el scorecard: ahí los botones no
son la respuesta.
"""
from __future__ import annotations

import json

from src.plugins.chats.agent.sales.card_texts import card_contents
from src.plugins.chats.agent.sales.decisions.facade import delivered_card_contents

QUEUED = json.dumps({"queued": True, "kind": "products_list", "count": 2})


def test_a_list_with_handles_names_its_products() -> None:
    args = {"handles": json.dumps(["velas-de-deseos-onduladas", "velas-de-deseos-cuadradas"]), "intro_text": "…"}

    assert card_contents("present_products", args, QUEUED) == (
        "Lista de productos: velas de deseos onduladas, velas de deseos cuadradas"
    )


def test_the_whole_catalog_says_so_and_how_many() -> None:
    result = json.dumps({
        "queued": True, "kind": "products_list", "count": 30, "total": 30,
        "products": [{"handle": f"p{i}", "title": f"Vela {i}"} for i in range(30)],
    })

    assert card_contents("present_products", {"group_by": "categories"}, result) == (
        "Lista con todo el catálogo: 30 productos"
    )


def test_a_category_names_the_category_and_its_products() -> None:
    result = json.dumps({
        "queued": True, "kind": "products_list", "count": 2, "category": "Decorativas", "total": 2,
        "products": [{"handle": "calabaza", "title": "Calabaza"}, {"handle": "momia", "title": "Momia"}],
    })

    assert card_contents("present_products", {"category": "decorativas"}, result) == (
        "Lista de la categoría «Decorativas»: Calabaza, Momia"
    )


def test_the_category_menu_is_a_menu_not_products() -> None:
    result = json.dumps({"queued": True, "kind": "categories", "count": 31})

    assert card_contents("present_products", {}, result) == "Menú de categorías del catálogo (31 productos)"


def test_a_long_list_names_the_first_ones_and_counts_the_rest() -> None:
    handles = [f"vela-{i}" for i in range(14)]

    assert card_contents("present_products", {"handles": handles}, QUEUED) == (
        "Lista de productos: " + ", ".join(f"vela {i}" for i in range(10)) + " y 4 más"
    )


def test_the_buttons_say_their_labels() -> None:
    buttons = json.dumps([{"id": "catalog.browse", "title": "Ver catálogo"}, {"id": "help.advice", "title": "Asesoría"}])

    assert card_contents("send_quick_replies", {"body": "¿Para qué ocasión?", "buttons": buttons}, "{}") == (
        "Botones: «Ver catálogo» · «Asesoría»"
    )


def test_a_product_card_and_its_photos_name_the_product() -> None:
    assert card_contents("present_product_detail", {"handle": "trilogia-del-terror"}, QUEUED) == (
        "Ficha del producto: trilogia del terror"
    )
    assert card_contents("present_product_gallery", {"handle": "calabaza"}, QUEUED) == "Fotos del producto: calabaza"


def test_a_card_without_contents_to_describe_says_nothing() -> None:
    assert card_contents("send_reply", {"text": "Hola"}, "{}") is None
    assert card_contents("present_products", {}, "no es json") is None


def test_the_turn_lists_what_each_delivered_card_shows_in_order() -> None:
    refused = json.dumps({"queued": False, "error": "catalog_choice_not_allowed"})
    events = [
        {"name": "search_products", "args": {"q": "velitas"}, "result": "{}"},
        {"name": "send_quick_replies", "args": {"buttons": [{"title": "Calabaza"}]}, "result": refused},
        {"name": "present_products", "args": {"handles": ["velas-de-deseos-onduladas"]}, "result": QUEUED},
        {"name": "send_reply", "args": {"text": "Te cuento"}, "result": "{}"},
    ]

    assert delivered_card_contents(events) == ["Lista de productos: velas de deseos onduladas"]


# ── Revisión de producción del PR #426 (2026-10-14) ───────────────────────

#: El tope con que el resultado de una tool viaja en `TurnResult.tool_events`.
_CUT = 4000


def test_a_whole_catalog_result_cut_in_the_trace_still_says_what_it_shows() -> None:
    """El catálogo completo con títulos reales pasa de 4000 caracteres y la
    traza lo corta: el JSON ya no se lee y ③ volvía a no ver la tarjeta
    (justo el caso del incidente)."""
    result = json.dumps({
        "queued": True, "kind": "products_list", "count": 30, "total": 30,
        "products": [{"handle": f"vela-artesanal-{i}", "title": f"Vela artesanal de cera de palma número {i}, tres capas de fragancia para regalo y decoración"}
                     for i in range(30)],
        "summary": "Lista de 30 productos enviada al cliente.",
    }, ensure_ascii=False)
    assert len(result) > _CUT

    assert card_contents("present_products", {}, result[:_CUT]) == "Lista con todo el catálogo: 30 productos"


def test_a_list_with_handles_survives_a_cut_result() -> None:
    args = {"handles": ["velas-de-deseos-cuadradas", "velas-de-deseos-onduladas"]}
    result = json.dumps({"queued": True, "kind": "products_list", "count": 2, "pages": 1, "missing": [],
                         "summary": "x" * _CUT})

    assert card_contents("present_products", args, result[:_CUT]) == (
        "Lista de productos: velas de deseos cuadradas, velas de deseos onduladas"
    )


def test_a_list_names_only_the_products_that_went_out() -> None:
    """Lo que la tool no encontró (`missing`), lo que no cupo (`left_out`) y lo
    incompleto (`incomplete`) no se le mostró al cliente."""
    args = {"handles": ["velas-de-deseos-cuadradas", "vela-inventada", "plegaria-de-luz", "calabaza"]}
    result = json.dumps({
        "queued": True, "kind": "products_list", "count": 1, "missing": ["vela-inventada"],
        "left_out": ["plegaria-de-luz"], "incomplete": [{"handle": "calabaza", "title": "Calabaza", "lacks": "precio"}],
    })

    assert card_contents("present_products", args, result) == "Lista de productos: velas de deseos cuadradas"
