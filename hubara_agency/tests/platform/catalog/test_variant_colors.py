"""Resolver puro color↔variante (Duo Zodiacal: cada signo viene en UN color).

El mapeo signo→color NO existe estructurado en Medusa (ni options, ni SKU,
ni metadata de variante — solo se ve en las fotos). La fuente machine-readable
es `product.metadata["colores"]` con formato operador-friendly:

    "Aries: rojo; Tauro: azul petróleo, verde azulado"

(entradas separadas por `;` o newline; aliases del color por coma). Este
módulo lo parsea y da el matching tolerante (case/acentos/género/número:
"ROJAS" matchea "rojo") que usan los choke points de sales para ofrecer
el mismo color en otro signo cuando la combinación pedida no existe.
"""
from __future__ import annotations

from src.platform.catalog import (
    colors_for_value,
    matching_color_alias,
    parse_variant_colors,
    primary_colors,
    values_for_color,
)

_MAPPING = parse_variant_colors(
    {
        "colores": (
            "Aries: rojo; Tauro: azul petróleo, verde azulado\n"
            "Capricornio: azul; Acuario = azul claro, celeste; "
            "Piscis: verde esmeralda; ; Leo: naranja"
        )
    }
)


# ---------- parseo ----------


def test_parse_basic_entries():
    assert _MAPPING["Aries"] == ["rojo"]
    assert _MAPPING["Tauro"] == ["azul petróleo", "verde azulado"]
    # `=` como separador y entradas vacías toleradas
    assert _MAPPING["Acuario"] == ["azul claro", "celeste"]
    assert list(_MAPPING) == [
        "Aries", "Tauro", "Capricornio", "Acuario", "Piscis", "Leo",
    ]


def test_parse_missing_or_empty_metadata():
    assert parse_variant_colors(None) == {}
    assert parse_variant_colors({}) == {}
    assert parse_variant_colors({"otro": "x"}) == {}
    assert parse_variant_colors({"colores": "   "}) == {}


# ---------- color pedido → signos que lo tienen ----------


def test_values_for_color_gender_and_number_tolerant():
    # "roja"/"ROJAS" deben matchear el alias "rojo"
    assert values_for_color(_MAPPING, "roja") == ["Aries"]
    assert values_for_color(_MAPPING, "ROJAS") == ["Aries"]


def test_values_for_color_accent_insensitive():
    assert values_for_color(_MAPPING, "azul petroleo") == ["Tauro"]


def test_values_for_color_partial_phrase():
    # "azul" matchea todos los azules (el bot ofrece y el cliente elige)
    assert values_for_color(_MAPPING, "azul") == [
        "Tauro", "Capricornio", "Acuario",
    ]
    assert values_for_color(_MAPPING, "celeste") == ["Acuario"]
    assert values_for_color(_MAPPING, "verde") == ["Tauro", "Piscis"]


def test_values_for_color_no_match():
    assert values_for_color(_MAPPING, "fucsia") == []
    assert values_for_color(_MAPPING, "") == []


# ---------- signo → su color ----------


def test_colors_for_value_case_and_accent_insensitive():
    assert colors_for_value(_MAPPING, "aries") == ["rojo"]
    assert colors_for_value(_MAPPING, "TAURO") == [
        "azul petróleo", "verde azulado",
    ]


def test_colors_for_value_unknown():
    assert colors_for_value(_MAPPING, "Virgo") == []
    assert colors_for_value(_MAPPING, "") == []


# ---------- helpers para los choke points ----------


def test_matching_color_alias_returns_canonical_catalog_casing():
    # lo que se persiste en el draft es el alias del catálogo, no lo dicho
    assert matching_color_alias(_MAPPING, "ROJAS") == "rojo"
    assert matching_color_alias(_MAPPING, "celeste") == "celeste"
    assert matching_color_alias(_MAPPING, "fucsia") is None


def test_primary_colors_dedupes_in_declaration_order():
    assert primary_colors(_MAPPING) == [
        "rojo", "azul petróleo", "azul", "azul claro",
        "verde esmeralda", "naranja",
    ]
