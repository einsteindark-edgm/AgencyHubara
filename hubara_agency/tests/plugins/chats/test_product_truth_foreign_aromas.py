"""Los aromas (o colores) que el texto le ofrece a un producto y ese producto no tiene (puro).

Incidente 2026-10-13 (bot V2): el cliente armó un carrito con 2 Velas de deseos
cuadradas; la ficha del producto decía «Chanel» y «Navidad» y el bot escribió
«vienen en Chanel, Drakar y Ylang Ylang». Drakar e Ylang Ylang son de la
Plegaria de Luz (salió en la misma búsqueda). El único aroma real era el de
Navidad. Esto encuentra los aromas del catálogo que el texto nombra y no son
del producto del que se habla; la decisión la toma la capacidad `aroma_ajeno`.
"""
from __future__ import annotations

import pytest

from src.plugins.chats.shared.product_truth import foreign_labels, mentioned_labels

#: Los aromas del catálogo como los escribe Medusa (con su error: «Ylan ylang»).
CATALOG = (
    "Caballero de la noche", "Limoncillo", "Lavanda", "Café", "Sándalo", "Ylan ylang",
    "Coco cremoso", "Frutos rojos", "Verde menta", "Drakar", "Chanel", "Noche de Navidad",
)
COLORS = ("gris", "Amarillo", "Café", "Lila")
DESEOS = ("Noche de Navidad",)
INCIDENT = "¿Qué aroma prefieres? Las de deseos cuadradas vienen en *Chanel*, *Drakar* y *Ylang Ylang*."


def test_the_incident_names_three_aromas_the_product_does_not_have() -> None:
    assert foreign_labels(INCIDENT, own=DESEOS, labels=CATALOG, others=COLORS) == ("Chanel", "Drakar", "Ylan ylang")


def test_the_real_aroma_of_the_product_is_never_foreign() -> None:
    text = "Las de deseos vienen solo en aroma *Noche de Navidad* 🎄"

    assert foreign_labels(text, own=DESEOS, labels=CATALOG, others=COLORS) == ()


@pytest.mark.parametrize(
    ("said", "label"),
    [
        ("Drakkar", "Drakar"),            # una letra doble de más
        ("Ylang Ylang", "Ylan ylang"),    # la escritura correcta contra el error del catálogo
        ("sandalo", "Sándalo"),           # sin tilde
        ("COCO CREMOSO", "Coco cremoso"),
    ],
)
def test_a_name_written_a_little_differently_is_still_the_catalog_aroma(said: str, label: str) -> None:
    assert mentioned_labels(f"La tenemos en {said}.", CATALOG) == (label,)


def test_a_short_name_admits_gender_and_number_but_no_typo() -> None:
    """«Café» y «Lila» (menos de 6 letras sin género ni número) no admiten una
    letra distinta: «cafés» y «lilas» sí, «lela» no."""
    assert mentioned_labels("la de cafe", ("Café",)) == ("Café",)
    assert mentioned_labels("unas cafés", ("Café",)) == ("Café",)
    assert mentioned_labels("las lilas", ("Lila",)) == ("Lila",)
    assert mentioned_labels("la lela", ("Lila",)) == ()


@pytest.mark.parametrize(
    ("said", "label"),
    [
        ("moradas", "Morado"),     # género y número: dos letras distintas
        ("azules", "Azul"),        # plural en -es
        ("rojas", "Rojo"),
        ("Kaballero de la nuche", "Caballero de la noche"),  # un nombre largo admite dos errores
    ],
)
def test_gender_number_and_long_names_are_tolerated(said: str, label: str) -> None:
    assert mentioned_labels(f"Te las dejo {said}.", (label,)) == (label,)


def test_a_name_that_is_also_a_color_is_left_out() -> None:
    """«Café» es aroma y color en el catálogo: «la caja trae café y lila» no
    dice nada del aroma. La regla no lo marca (sin certeza no se frena)."""
    text = "La caja Multicolor trae velas gris, café y lila."

    assert foreign_labels(text, own=DESEOS, labels=CATALOG, others=COLORS) == ()


def test_a_longer_name_wins_over_the_word_inside_it() -> None:
    """«Noche de Navidad» no es también «Navidad» ni «Caballero de la noche»."""
    assert mentioned_labels("Huele a Noche de Navidad", ("Navidad", "Noche de Navidad")) == ("Noche de Navidad",)


def test_ordinary_words_are_not_aromas() -> None:
    text = "Perfecto, dos velas de deseos cuadradas. Te llegan en 3 días a Bogotá."

    assert mentioned_labels(text, CATALOG) == ()


def test_without_the_aromas_of_the_product_nothing_is_judged() -> None:
    """Un producto sin aromas en el catálogo (dato que falta) no se juzga."""
    assert foreign_labels(INCIDENT, own=(), labels=CATALOG, others=COLORS) == ()


@pytest.mark.parametrize(
    "text",
    [
        "Las de deseos no vienen en Chanel ni en Drakar: su único aroma es Noche de Navidad.",
        "Lavanda no la manejamos en esta referencia, lo siento.",
        "Tampoco en Sándalo, solo en Noche de Navidad.",
    ],
)
def test_an_aroma_the_text_denies_is_not_offered(text: str) -> None:
    """La regla decide sola mientras la decisión está en `reglas`/sombra: lo
    que el mismo trozo de la oración niega no se le ofrece al cliente."""
    assert foreign_labels(text, own=DESEOS, labels=CATALOG, others=COLORS) == ()


def test_a_negation_in_another_clause_does_not_hide_the_offer() -> None:
    text = "No te preocupes, te las dejo en Drakar. ¿Te sirve?"

    assert foreign_labels(text, own=DESEOS, labels=CATALOG, others=COLORS) == ("Drakar",)


# ── Colores: la misma búsqueda con otra lista (capacidad `color_ajeno`) ──

ALL_COLORS = ("gris", "Amarillo", "verde", "Naranja", "Morado", "Azul", "Negro", "Blanco", "Lila", "Rosado", "Café")
#: Las Velas de deseos se escogen por presentación (la opción «Color» de Medusa).
DESEOS_COLORS = ("Rojo y verde", "Multicolor")


def test_colors_of_other_products_are_foreign() -> None:
    text = "Te las dejo en azul y lila, ¿te sirve?"

    assert foreign_labels(text, own=DESEOS_COLORS, labels=ALL_COLORS, others=CATALOG) == ("Azul", "Lila")


def test_a_color_of_the_product_wins_over_the_word_inside_it() -> None:
    """«Rojo y verde» es la presentación de las velas de deseos: su «verde» no es otro color."""
    text = "Vienen en Rojo y verde o en Multicolor. ¿Cuál prefieres?"

    assert foreign_labels(text, own=DESEOS_COLORS, labels=ALL_COLORS, others=CATALOG) == ()


def test_a_color_written_in_feminine_is_the_same_color() -> None:
    text = "En morada queda hermosa 🤍"

    assert foreign_labels(text, own=ALL_COLORS, labels=ALL_COLORS, others=CATALOG) == ()
    assert foreign_labels(text, own=DESEOS_COLORS, labels=ALL_COLORS, others=CATALOG) == ("Morado",)


# ── Lo que el texto afirma y lo que niega (pago, estado, negación falsa) ──

from src.plugins.chats.shared.product_truth import affirms, denied_labels  # noqa: E402

COD = r"contra\s*entrega|pag\w*\s+(?:al|cuando)\s+(?:recibir|te\s+llegue)"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Claro, puedes pagar contra entrega 🤍", True),
        ("Lo pagas al recibir, sin problema.", True),
        ("Este pedido no aplica para contra entrega.", False),        # lo niega su cláusula
        ("Contra entrega no aplica para este pedido.", False),        # …antes o justo después
        ("No te preocupes, puedes pagar contra entrega.", True),       # la negación es de otra cláusula
        ("Te paso el link de pago por Nequi.", False),
    ],
)
def test_affirms_finds_a_claim_its_clause_does_not_deny(text: str, expected: bool) -> None:
    assert affirms(text, COD) is expected


TITLES = ("Plegaria de Luz", "Velas de deseos cuadradas")


@pytest.mark.parametrize(
    ("text", "labels", "expected"),
    [
        ("Esa no la tenemos: la Plegaria de Luz ya no la manejamos.", TITLES, ("Plegaria de Luz",)),
        ("No tenemos Plegaria de Luz por ahora.", TITLES, ("Plegaria de Luz",)),
        ("Lavanda no la manejamos en esta referencia.", ("Lavanda",), ("Lavanda",)),
        ("¿No te gustaría la Plegaria de Luz?", TITLES, ()),          # un «no» que no niega tenerla
        ("La Plegaria de Luz sí la tenemos 🤍", TITLES, ()),
    ],
)
def test_denied_labels_are_the_ones_a_denial_verb_takes(text: str, labels: tuple, expected: tuple) -> None:
    assert denied_labels(text, labels) == expected
