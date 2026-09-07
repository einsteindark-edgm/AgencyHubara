"""Precio server-side de un pedido desde el snapshot del catálogo (D1.2b).

Meta Business Agent NO manda precios en `register_order` (items = handle +
variant_label + quantity). `chats` los recalcula desde el snapshot antes de
llamar `RegisterOrderTool` (SEC-07 exige subtotal = Σ unit_price×qty). La
tarifa de envío es la MÍNIMA por ciudad (regla #241: nunca definitiva).
"""
from __future__ import annotations

import pytest

from src.platform.catalog.dtos import CatalogPriceDTO, CatalogProductDTO, CatalogVariantDTO
from src.plugins.chats.agent.sales.config.shipping import (
    SHIPPING_RATE_BOGOTA_COP,
    SHIPPING_RATE_NATIONAL_COP,
    shipping_rate_for_city,
)
from src.plugins.chats.agent.sales.use_cases.order_pricing import price_order_items

_LUZ = CatalogProductDTO(
    id="p1", handle="luz-serena", title="Luz Serena", status="published",
    variants=[
        CatalogVariantDTO(id="v1", title="Lavanda / Blanco", options={"Aroma": "Lavanda", "Color": "Blanco"}, prices=[
            CatalogPriceDTO(amount="9.00", currency_code="usd"), CatalogPriceDTO(amount="29000", currency_code="cop"),
        ]),
        CatalogVariantDTO(id="v2", title="Vainilla / Rojo", options={"Aroma": "Vainilla", "Color": "Rojo"}, prices=[
            CatalogPriceDTO(amount="31000.00", currency_code="cop"),
        ]),
    ],
)
_ZODIAC = CatalogProductDTO(
    id="p2", handle="duo-zodiacal", title="Dúo Zodiacal", status="published",
    description="Set con portavelas.",
    variants=[
        CatalogVariantDTO(id="z1", title="Leo", options={"Signo": "Leo"}, prices=[CatalogPriceDTO(amount="52000", currency_code="cop")]),
        CatalogVariantDTO(id="z2", title="Aries", options={"Signo": "Aries"}, prices=[CatalogPriceDTO(amount="52000", currency_code="cop")]),
    ],
)
_SIMPLE = CatalogProductDTO(
    id="p3", handle="velon", title="Velón", status="published",
    variants=[CatalogVariantDTO(id="s1", title="Unico", prices=[CatalogPriceDTO(amount="12000", currency_code="cop")])],
)
_BY_HANDLE = {p.handle: p for p in (_LUZ, _ZODIAC, _SIMPLE)}


def test_variant_label_resolves_the_variant_price_in_cop() -> None:
    priced = price_order_items(_BY_HANDLE, [
        {"handle": "luz-serena", "variant_label": "Vainilla, Rojo", "quantity": 2},
        {"handle": "duo-zodiacal", "variant_label": "Leo", "quantity": 1},
        {"handle": "velon", "quantity": 3},
    ])
    assert priced.problems == []
    assert [(i["handle"], i["unit_price_cop"], i["quantity"]) for i in priced.items] == [
        ("luz-serena", 31000, 2), ("duo-zodiacal", 52000, 1), ("velon", 12000, 3),
    ]
    assert priced.items[0]["variant_label"] == "Vainilla, Rojo"
    assert priced.items[0]["title"] == "Luz Serena"
    assert priced.subtotal_cop == 31000 * 2 + 52000 + 12000 * 3


def test_label_matching_is_case_and_accent_insensitive_and_falls_back_to_first_variant() -> None:
    priced = price_order_items(_BY_HANDLE, [
        {"handle": "luz-serena", "variant_label": "lavanda, blanco", "quantity": 1},
        {"handle": "luz-serena", "variant_label": "Canela, Verde", "quantity": 1},  # no existe
    ])
    assert priced.problems == []
    assert priced.items[0]["unit_price_cop"] == 29000 and priced.items[0]["variant_resolved"] is True
    assert priced.items[1]["unit_price_cop"] == 29000 and priced.items[1]["variant_resolved"] is False


def test_unknown_handle_or_bad_quantity_is_a_problem_not_a_crash() -> None:
    priced = price_order_items(_BY_HANDLE, [
        {"handle": "no-existe", "quantity": 1},
        {"handle": "velon", "quantity": 0},
    ])
    assert priced.items == []
    assert priced.problems == ["unknown_product:no-existe", "invalid_quantity:velon"]


@pytest.mark.parametrize("city,rate", [
    ("Bogotá", SHIPPING_RATE_BOGOTA_COP), ("bogota d.c.", SHIPPING_RATE_BOGOTA_COP), ("BOGOTÁ", SHIPPING_RATE_BOGOTA_COP),
    ("Medellín", SHIPPING_RATE_NATIONAL_COP), ("Cali", SHIPPING_RATE_NATIONAL_COP), ("", SHIPPING_RATE_NATIONAL_COP),
])
def test_minimum_shipping_rate_by_city(city: str, rate: int) -> None:
    assert shipping_rate_for_city(city) == rate
