"""Meta items are keyed by SKU and handle, never by Medusa ids (2026-09-14).

Medusa regenerates `prod_…` / `variant_…` whenever a product is deleted and
re-created — the live catalog shows three products and four variants that
already went through that — and every re-creation duplicated the item in the
Meta catalog and orphaned what the bot referenced. The SKU is the identity the
uploader now loads and the one Google's feed and the storefront publish, so
`retailer_id` carries it and `item_group_id` carries the product handle.
"""
from __future__ import annotations

import logging

from src.platform.catalog.dtos import (
    CatalogImageDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
)
from src.platform.meta_catalog.mapper import map_products_batch


def _cop(amount: str) -> list[CatalogPriceDTO]:
    return [CatalogPriceDTO(amount=amount, currency_code="cop")]


def _duo(*, skus: bool = True) -> CatalogProductDTO:
    return CatalogProductDTO(
        id="prod_duo_v2",
        handle="duo-zodiacal",
        title="Duo Zodiacal",
        status="published",
        description="Set de dos velas del signo que elijas.",
        thumbnail="https://assets.hubara.com.co/00-portada-x.webp",
        options={"Signo": ["Leo", "Escorpion"]},
        variants=[
            CatalogVariantDTO(
                id="v_leo", title="Leo", sku="HUB-DUOZOD-LEO" if skus else None,
                options={"Signo": "Leo"}, prices=_cop("35000"),
            ),
            CatalogVariantDTO(
                id="v_esc", title="Escorpion", sku="HUB-DUOZOD-ESCORPIO" if skus else None,
                options={"Signo": "Escorpion"}, prices=_cop("35000"),
            ),
        ],
        images=[CatalogImageDTO(url="https://assets.hubara.com.co/Leo-x.webp", rank=0)],
    )


def _legacy(*, sku: str | None = "HUB-SACRIFICIO") -> CatalogProductDTO:
    return CatalogProductDTO(
        id="prod_legacy",
        handle="sacrificio-de-amor",
        title="Sacrificio de Amor",
        status="published",
        description="desc",
        thumbnail="https://assets.hubara.com.co/banner-x.webp",
        variants=[CatalogVariantDTO(id="v1", title="Unico", sku=sku, prices=_cop("19000"))],
    )


def test_variant_items_are_keyed_by_sku_and_grouped_by_handle():
    items, _ = map_products_batch([_duo()])

    assert sorted(i.retailer_id for i in items) == ["HUB-DUOZOD-ESCORPIO", "HUB-DUOZOD-LEO"]
    assert {i.item_group_id for i in items} == {"duo-zodiacal"}


def test_single_variant_product_is_keyed_by_its_variant_sku():
    items, _ = map_products_batch([_legacy()])

    assert [i.retailer_id for i in items] == ["HUB-SACRIFICIO"]
    assert items[0].item_group_id is None


def test_variant_without_sku_keeps_the_medusa_id_and_warns(caplog):
    # Migration window: the uploader has not loaded SKUs yet. Dropping the item
    # would empty the WhatsApp catalog; keeping the old key is the lesser evil,
    # but the operator must see it.
    with caplog.at_level(logging.WARNING, logger="src.platform.meta_catalog.mapper"):
        items, _ = map_products_batch([_duo(skus=False)])

    assert sorted(i.retailer_id for i in items) == ["v_esc", "v_leo"]
    assert any("sin SKU" in r.getMessage() and "Duo Zodiacal" in r.getMessage() for r in caplog.records)


def test_single_variant_product_without_sku_keeps_the_product_id_and_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="src.platform.meta_catalog.mapper"):
        items, _ = map_products_batch([_legacy(sku=None)])

    assert [i.retailer_id for i in items] == ["prod_legacy"]
    assert any("sin SKU" in r.getMessage() for r in caplog.records)
