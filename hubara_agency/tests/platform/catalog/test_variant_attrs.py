"""Tests de `platform.catalog.variant_attrs` — listas cerradas desde tags.

El caso que motiva todo (ep_010, run fa1eb974): el catalogo real de Cruz de
Vida tiene 11 aromas y 9 colores como tags `"Aroma: X"` / `"Color: y"`; el LLM
recito "14 aromas y 10 colores" e invento "Melocotón"/"Crema". Estas funciones
son la base del enforcement determinista (picker + order draft + juez).
"""
from __future__ import annotations

from src.platform.catalog.variant_attrs import (
    match_option,
    normalize_label,
    parse_variant_tags,
    split_multi_label,
)

# Tags REALES de Cruz de Vida (copiados del snapshot del vault, mayo 2026):
# mezcla casing ("Color: gris" en minuscula, "Color: Amarillo" capitalizado).
_CRUZ_TAGS = [
    "Aroma: Caballero de la noche",
    "Aroma: Limoncillo",
    "Color: gris",
    "Color: Amarillo",
    "Color: verde",
    "Aroma: Lavanda",
    "Aroma: Café",
    "Aroma: Sándalo",
    "Aroma: Ylan ylang",
    "Aroma: Coco cremoso",
    "Aroma: Frutos rojos",
    "Aroma: Verde menta",
    "Aroma: Drakar",
    "Aroma: Chanel",
    "Color: Naranja",
    "Color: Morado",
    "Color: Azul",
    "Color: Blanco",
    "Color: Lila",
    "Color: Rosado",
]


def test_parse_real_cruz_de_vida_tags():
    attrs = parse_variant_tags(_CRUZ_TAGS)
    assert len(attrs.aromas) == 11
    assert len(attrs.colors) == 9
    assert "Drakar" in attrs.aromas and "Café" in attrs.aromas
    assert "gris" in attrs.colors and "Blanco" in attrs.colors
    # Lo que ep_010 inventó NO está:
    assert match_option("Melocotón", attrs.colors) is None
    assert match_option("Crema", attrs.colors) is None


def test_parse_dedupes_and_ignores_unknown_prefixes():
    attrs = parse_variant_tags(
        ["Aroma: Lavanda", "aroma: lavanda", "Categoria: religiosa", "suelto"]
    )
    assert attrs.aromas == ["Lavanda"]  # dedupe normalizado, casing del 1ro
    assert attrs.colors == []


def test_parse_tolerates_none_and_empty():
    assert parse_variant_tags(None).aromas == []
    assert parse_variant_tags(["Aroma:   "]).aromas == []


def test_normalize_label_strips_accents_and_case():
    assert normalize_label("Melocotón") == "melocoton"
    assert normalize_label("  Verde  Menta ") == "verde menta"
    assert normalize_label("CAFÉ") == "cafe"


def test_match_option_is_accent_and_case_insensitive():
    valid = ["Café", "Verde menta", "Drakar"]
    assert match_option("cafe", valid) == "Café"  # devuelve casing canonico
    assert match_option("VERDE MENTA", valid) == "Verde menta"
    assert match_option("drakar", valid) == "Drakar"
    assert match_option("vainilla", valid) is None
    assert match_option("", valid) is None


def test_split_multi_label_variants():
    assert split_multi_label("Drakar, Café") == ["Drakar", "Café"]
    assert split_multi_label("Drakar y Café") == ["Drakar", "Café"]
    assert split_multi_label("Drakar / Café; Lavanda") == [
        "Drakar", "Café", "Lavanda",
    ]
    assert split_multi_label("Drakar") == ["Drakar"]
    # "y" SOLO como separador con espacios (no parte "Ylan ylang")
    assert split_multi_label("Ylan ylang") == ["Ylan ylang"]
