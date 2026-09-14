"""The retailer_id the bot sends WhatsApp is the SKU (2026-09-14).

Meta's catalog is keyed by SKU now (platform/meta_catalog/mapper.py). A product
card or an order confirmation that still referenced the Medusa id would be
dropped by WhatsApp silently — the same failure test_ui_intents_variant_retailer
documents for the Duo Zodiacal, one identity later.
"""
from __future__ import annotations

from src.platform.catalog.dtos import (
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
)
from src.plugins.chats.agent.sales.tools.ui_intents import _meta_retailer_id


def _variant(id_: str, title: str, sku: str | None, options: dict | None = None) -> CatalogVariantDTO:
    return CatalogVariantDTO(
        id=id_, title=title, sku=sku, options=options,
        prices=[CatalogPriceDTO(amount="35000", currency_code="cop")],
    )


def _product(handle: str, variants: list[CatalogVariantDTO], options: dict | None = None) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=f"prod_{handle}", handle=handle, title=handle, status="published",
        options=options, variants=variants,
    )


def test_option_product_uses_the_first_variant_sku():
    duo = _product(
        "duo-zodiacal",
        [_variant("v_leo", "Leo", "HUB-DUOZOD-LEO", {"Signo": "Leo"}), _variant("v_esc", "Escorpion", "HUB-DUOZOD-ESCORPIO", {"Signo": "Escorpion"})],
        options={"Signo": ["Leo", "Escorpion"]},
    )
    assert _meta_retailer_id(duo) == "HUB-DUOZOD-LEO"


def test_single_variant_product_uses_its_variant_sku():
    serena = _product("luz-serena", [_variant("v_unico", "Unico", "HUB-SERENA")])
    assert _meta_retailer_id(serena) == "HUB-SERENA"


def test_without_sku_falls_back_to_the_medusa_ids_the_catalog_still_carries():
    duo = _product(
        "duo-zodiacal",
        [_variant("v_leo", "Leo", None, {"Signo": "Leo"}), _variant("v_esc", "Escorpion", None, {"Signo": "Escorpion"})],
        options={"Signo": ["Leo", "Escorpion"]},
    )
    serena = _product("luz-serena", [_variant("v_unico", "Unico", None)])

    assert _meta_retailer_id(duo) == "v_leo"
    assert _meta_retailer_id(serena) == "prod_luz-serena"
