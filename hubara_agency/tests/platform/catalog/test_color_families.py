"""Familias de color — el cliente pide un TONO, el catálogo ofrece una FAMILIA.

Caso del operador (2026-09-08): "¿lo tienen en azul clarito / azul mar?" con
el catálogo ofreciendo "Azul" terminaba en un "ese color no lo manejo"
cortante, porque `match_option` es exacto. Estas funciones puras resuelven el
tono a la familia del catálogo; la tabla de familias es CONFIGURABLE por
tenant (`config/color_families/families.yaml`).
"""
from __future__ import annotations

import pytest

from src.platform.catalog.color_families import (
    EMPTY_COLOR_FAMILIES,
    InvalidColorFamiliesError,
    family_of_color,
    parse_color_families,
    resolve_color_family,
)

_DOC = {
    "version": 1,
    "modifiers": ["claro", "clarito", "oscuro", "pastel"],
    "families": {
        "blanco": {"label": "Blanco", "shades": ["blanco", "hueso", "crema"]},
        "rojo": {"label": "Rojo", "shades": ["rojo", "vinotinto", "vino tinto"]},
        "rosado": {"label": "Rosado", "shades": ["rosado", "rosa", "fucsia"]},
        "azul": {
            "label": "Azul",
            "shades": ["azul", "celeste", "azul mar", "marino", "turquesa"],
        },
        "verde": {"label": "Verde", "shades": ["verde", "menta", "oliva"]},
        "lila": {"label": "Lila", "shades": ["lila", "lavanda", "morado clarito"]},
        "morado": {"label": "Morado", "shades": ["morado", "violeta"]},
    },
}
FAM = parse_color_families(_DOC)
CATALOG = ["Blanco", "gris", "Rosado", "Morado", "Lila", "Azul"]


# ---------- parse ----------


def test_parse_keeps_declaration_order_and_labels():
    assert [f.id for f in FAM.families] == [
        "blanco", "rojo", "rosado", "azul", "verde", "lila", "morado",
    ]
    assert FAM.families[3].label == "Azul"
    assert "celeste" in FAM.families[3].shades
    assert FAM.version == 1
    assert "clarito" in FAM.modifiers
    assert not FAM.is_empty


def test_parse_rejects_bad_schema():
    with pytest.raises(InvalidColorFamiliesError):
        parse_color_families({"version": 1, "families": ["azul"]})
    with pytest.raises(InvalidColorFamiliesError):
        parse_color_families({"version": 1, "families": {"azul": {"shades": []}}})
    with pytest.raises(InvalidColorFamiliesError):
        parse_color_families("no soy un dict")


def test_parse_family_without_shades_still_matches_its_label():
    fam = parse_color_families(
        {"version": 1, "families": {"negro": {"label": "Negro"}}}
    )
    assert family_of_color("NEGRAS", fam).id == "negro"


# ---------- family_of_color (colores del catálogo) ----------


def test_family_of_catalog_colors_is_accent_case_gender_insensitive():
    assert family_of_color("Azul", FAM).id == "azul"
    assert family_of_color("ROJAS", FAM).id == "rojo"
    assert family_of_color("Lila", FAM).id == "lila"
    assert family_of_color("Azul marino", FAM).id == "azul"
    assert family_of_color("gris", FAM) is None  # no declarada en este doc


# ---------- resolve_color_family ----------


def test_shade_resolves_to_the_single_catalog_color_of_its_family():
    res = resolve_color_family("azul clarito", CATALOG, FAM)
    assert res is not None
    assert res.status == "resolved"
    assert res.canonical == "Azul"
    assert res.families == ("Azul",)
    assert res.shade_requested is True  # pidió un tono: el bot lo confirma


@pytest.mark.parametrize(
    "requested",
    ["azul mar", "celeste", "CELESTE", "azul marino", "turquesa", "un azul oscuro"],
)
def test_common_spanish_blue_shades_resolve_to_azul(requested):
    res = resolve_color_family(requested, CATALOG, FAM)
    assert res is not None and res.canonical == "Azul", requested


def test_plain_gender_number_variation_is_not_a_shade_request():
    res = resolve_color_family("azules", CATALOG, FAM)
    assert res is not None
    assert res.canonical == "Azul"
    assert res.shade_requested is False


def test_exact_catalog_label_wins_over_family_grouping():
    # "Lila" existe tal cual: se captura Lila aunque "morado clarito" también
    # esté en la familia lila.
    res = resolve_color_family("lila", CATALOG, FAM)
    assert res is not None and res.canonical == "Lila"


def test_more_specific_shade_beats_generic_family_word():
    # "morado clarito" está declarado en LILA; el genérico "morado" (familia
    # morado) no debe ganar por contener la palabra.
    res = resolve_color_family("morado clarito", CATALOG, FAM)
    assert res is not None
    assert res.families == ("Lila",)
    assert res.canonical == "Lila"


def test_fucsia_resolves_to_rosado():
    res = resolve_color_family("fucsia", CATALOG, FAM)
    assert res is not None and res.canonical == "Rosado"


def test_family_with_several_catalog_colors_is_ambiguous_not_guessed():
    res = resolve_color_family("azul clarito", ["Azul", "Azul marino"], FAM)
    assert res is not None
    assert res.status == "ambiguous"
    assert res.canonical is None
    assert res.candidates == ("Azul", "Azul marino")


def test_two_families_in_one_request_is_ambiguous_with_both_candidates():
    res = resolve_color_family("azul o verde", ["Azul", "Verde", "Blanco"], FAM)
    assert res is not None
    assert res.status == "ambiguous"
    assert res.families == ("Azul", "Verde")
    assert res.candidates == ("Azul", "Verde")


def test_family_not_offered_by_catalog():
    res = resolve_color_family("vinotinto", CATALOG, FAM)
    assert res is not None
    assert res.status == "not_offered"
    assert res.families == ("Rojo",)
    assert res.canonical is None
    assert res.candidates == ()


def test_unknown_word_has_no_family():
    assert resolve_color_family("chartreuse", CATALOG, FAM) is None
    assert resolve_color_family("   ", CATALOG, FAM) is None


def test_empty_families_never_resolve():
    assert resolve_color_family("azul clarito", CATALOG, EMPTY_COLOR_FAMILIES) is None
    assert family_of_color("Azul", EMPTY_COLOR_FAMILIES) is None
