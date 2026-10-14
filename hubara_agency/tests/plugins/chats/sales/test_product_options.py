"""De qué productos habla un texto, qué tiene cada uno (aromas, colores,
presentaciones) y el mensaje con que vuelve al modelo: entrada y salida de
`aroma_ajeno`, `color_ajeno` y `variante_ajena` (incidente 2026-10-13). Puro."""
from __future__ import annotations

from types import SimpleNamespace

from src.plugins.chats.agent.sales.use_cases.reply_facts import Findings, ReplyFacts, fact_message
from src.plugins.chats.agent.sales.use_cases.product_options import (
    colors_of,
    focus_lines,
    focus_products,
    option_sheets,
    presentations_of,
    single_sheets,
)


def _product(title: str, handle: str, aromas: list[str], colors: list[str] = (), options: dict | None = None,
             metadata: dict | None = None) -> SimpleNamespace:
    tags = [f"Aroma: {a}" for a in aromas] + [f"Color: {c}" for c in colors]
    return SimpleNamespace(title=title, handle=handle, tags=tags, options=options or {"Unico": ["Unico"]},
                           metadata=metadata or {})


DESEOS_OPTIONS = {"Color": ["Rojo y verde", "Multicolor"]}
CUADRADAS = _product("Velas de deseos cuadradas", "velas-de-deseos-cuadradas", ["Noche de Navidad"], options=DESEOS_OPTIONS)
ONDULADAS = _product("Velas de deseos onduladas", "velas-de-deseos-onduladas", ["Noche de Navidad"], options=DESEOS_OPTIONS)
PLEGARIA = _product("Plegaria de Luz", "plegaria-de-luz", ["Lavanda", "Drakar", "Chanel"], ["Lila", "Azul"])
DUO = _product(
    "Duo Zodiacal", "duo-zodiacal", ["Lavanda"], ["Lila", "Azul", "Negro"],
    options={"Signo": ["Aries", "Leo"]}, metadata={"colores": "Aries: Rojo, rojizo; Leo: Dorado"},
)
SERENA = _product("Luz Serena", "luz-serena", ["Lavanda"])
CORAZON = _product("Cubo de corazón", "cubo-de-corazon", ["Drakar"])
HELLOWEN = _product("Hellowen", "hellowen", [])
CATALOG = [CUADRADAS, ONDULADAS, PLEGARIA, SERENA, CORAZON, HELLOWEN]


def _draft(*names: str) -> dict:
    return {"items": [{"producto": n, "cantidad": "1"} for n in names]}


def test_the_order_is_the_focus() -> None:
    assert focus_products(CATALOG, draft=_draft("Velas de deseos cuadradas"), text="Vienen en Drakar") == [CUADRADAS]


def test_a_product_named_by_a_word_only_its_title_has_joins_the_focus() -> None:
    """«la plegaria» es la Plegaria de Luz; «luz» sola no (la tienen dos)."""
    text = "La plegaria sí viene en Drakar."

    assert focus_products(CATALOG, draft=_draft("Velas de deseos cuadradas"), text=text) == [CUADRADAS, PLEGARIA]
    assert focus_products(CATALOG, draft=None, text="Esa luz es hermosa") == []


def test_a_word_two_titles_share_names_both() -> None:
    """«las de deseos» son las dos velas de deseos (los mismos aromas)."""
    assert focus_products(CATALOG, draft=None, text="Las de deseos vienen en Drakar") == [CUADRADAS, ONDULADAS]


def test_a_shared_word_names_the_product_of_the_order_not_its_sibling() -> None:
    """Premortem del PR #426: con las cuadradas en el pedido, «las de deseos»
    sumaba las onduladas y sus aromas pasaban por propios."""
    draft = _draft("Velas de deseos cuadradas")

    assert focus_products(CATALOG, draft=draft, text="Las de deseos vienen en Drakar") == [CUADRADAS]


def test_a_product_of_the_order_missing_from_the_catalog_is_not_judged() -> None:
    assert focus_products(CATALOG, draft=_draft("Vela que ya no existe"), text="Viene en Drakar") == []


def test_what_jev_reads_has_each_product_with_its_aromas() -> None:
    assert focus_lines([CUADRADAS, PLEGARIA]) == (
        "Velas de deseos cuadradas: Noche de Navidad",
        "Plegaria de Luz: Lavanda, Drakar, Chanel",
    )
    assert focus_lines([HELLOWEN]) == ("Hellowen: sin aromas en el catálogo",)


def test_the_message_names_the_real_aroma_and_the_way_out() -> None:
    message = _message([CUADRADAS], aromas=["Chanel", "Drakar", "Ylan ylang"])

    assert "«Chanel», «Drakar» y «Ylan ylang» como aromas de Velas de deseos cuadradas" in message
    assert "Su único aroma es Noche de Navidad." in message
    assert "vuelve a llamar send_reply con el mismo texto" in message  # otro producto o «no lo tiene»


def test_the_message_with_two_products_says_which_aromas_are_whose() -> None:
    message = _message([CUADRADAS, HELLOWEN], aromas=["Drakar"])

    assert "«Drakar» como aroma de Velas de deseos cuadradas o Hellowen" in message
    assert "Velas de deseos cuadradas: Noche de Navidad; Hellowen: sin aromas en el catálogo." in message


def test_the_colors_of_a_product_are_its_tags_or_its_color_option() -> None:
    """Las velas de deseos se escogen por la opción «Color» de Medusa (Multicolor,
    Rojo y verde); la Plegaria, por sus etiquetas «Color: …»."""
    assert colors_of(CUADRADAS) == ["Rojo y verde", "Multicolor"]
    assert colors_of(PLEGARIA) == ["Lila", "Azul"]


def test_with_colors_by_variant_the_real_palette_is_the_variants_one() -> None:
    """Duo Zodiacal: el mapeo signo→color de la metadata manda; las etiquetas pueden estar viejas."""
    assert colors_of(DUO) == ["Rojo", "Dorado"]


def test_only_real_options_are_presentations() -> None:
    assert presentations_of(CUADRADAS) == ["Rojo y verde", "Multicolor"]
    assert presentations_of(DUO) == ["Aries", "Leo"]
    assert presentations_of(PLEGARIA) == []  # «Unico» es el relleno de Medusa
    assert presentations_of(_product("Una", "una", [], options={"Tamaño": ["Unico"]})) == []


def test_jev_reads_a_sheet_only_for_products_with_presentations() -> None:
    [sheet] = option_sheets([CUADRADAS, PLEGARIA])

    assert sheet == (
        "Producto del pedido (del que hablan): Velas de deseos cuadradas.\n"
        "Aromas que tiene: Noche de Navidad.\n"
        "Colores que tiene: Rojo y verde, Multicolor.\n"
        "Presentaciones que tiene: Rojo y verde, Multicolor."
    )
    assert option_sheets([PLEGARIA]) == ()


def test_the_message_names_foreign_colors_and_the_real_ones() -> None:
    message = _message([PLEGARIA], aromas=["Noche de Navidad"], colors=["Rojo"])

    assert "«Noche de Navidad» como aroma y «Rojo» como color de Plegaria de Luz, y ese producto no los tiene" in message
    assert "Sus aromas son: Lavanda, Drakar, Chanel." in message and "Sus colores son: Lila, Azul." in message


def test_the_message_for_a_presentation_it_does_not_have() -> None:
    """Jev no dice cuál: el mensaje le da al modelo las que sí tiene."""
    message = _message([DUO], variant=True)

    assert "Le ofreciste a Duo Zodiacal una presentación, diseño o tamaño que no tiene" in message
    assert "Sus presentaciones son: Aries, Leo." in message
    assert "vuelve a llamar send_reply con el mismo texto" in message


def test_a_title_word_names_its_product_in_plural_too() -> None:
    """«los corazones» es el Cubo de corazón (sin género ni número, la regla del catálogo)."""
    assert focus_products(CATALOG, draft=None, text="Los corazones vienen en Drakar") == [CORAZON]



def test_a_product_of_a_single_presentation_has_its_own_sheet() -> None:
    [sheet] = single_sheets([PLEGARIA, CUADRADAS])

    assert sheet.startswith("Producto del pedido (del que hablan): Plegaria de Luz.")
    assert sheet.endswith("Presentaciones que tiene: una sola (no viene en otras formas, envases, tamaños ni versiones).")


def test_the_message_for_a_single_presentation_says_it() -> None:
    message = _message([PLEGARIA], variant=True)

    assert "Viene en una sola presentación" in message



def _message(focus, **found) -> str:
    """El mensaje con que el texto vuelve al modelo (el camino real: `fact_message`)."""
    return fact_message(Findings(**{k: tuple(v) if isinstance(v, list) else v for k, v in found.items()}),
                        ReplyFacts(focus=tuple(focus)))
