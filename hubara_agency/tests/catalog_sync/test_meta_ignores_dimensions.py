"""Las medidas del snapshot NO viajan a Meta ni disparan updates (2026-09-23).

Las medidas se agregaron al catálogo local para que las consulte el agente
de ventas. El operador pidió explícitamente que no alteren el sync con Meta:
el item publicado y su hash (el que decide create/update/no-op) tienen que
ser idénticos con o sin medidas. Si alguien mapea `dimensions` al item, este
test lo caza antes de que un deploy re-empuje los 29 productos.
"""
from __future__ import annotations

from dataclasses import replace

from src.platform.catalog.dtos import (
    CatalogDimensionsDTO,
    CatalogImageDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
)
from src.platform.meta_catalog.mapper import map_products_batch
from src.plugins.catalog.agent.use_cases.push_meta_catalog import _hash_item

_SIGNOS = ["Aries", "Leo"]

_SIMPLE = CatalogProductDTO(
    id="prod_calabaza",
    handle="calabaza",
    title="Calabaza",
    status="published",
    description="Mini escultura de calabaza.",
    thumbnail="https://assets.hubara.com.co/calabaza.webp",
    variants=[
        CatalogVariantDTO(
            id="v_calabaza",
            title="Unico",
            prices=[CatalogPriceDTO(amount="16000", currency_code="cop")],
        )
    ],
    images=[CatalogImageDTO(url="https://assets.hubara.com.co/calabaza.webp")],
    tags=["Aroma: Frutos rojos", "Color: Naranja"],
    categories=["decorativas"],
)

# Con options reales el mapper emite un item POR variante: también se cubre.
_DUO = replace(
    _SIMPLE,
    id="prod_duo",
    handle="duo-zodiacal",
    title="Duo Zodiacal",
    options={"Signo": _SIGNOS},
    variants=[
        CatalogVariantDTO(
            id=f"v_{s.lower()}",
            title=s,
            prices=[CatalogPriceDTO(amount="35000", currency_code="cop")],
            options={"Signo": s},
        )
        for s in _SIGNOS
    ],
)

_DIMS = CatalogDimensionsDTO(height_cm=9.0, width_cm=6.0, weight_g=120.0)


def test_meta_items_identical_with_or_without_dimensions():
    products = [_SIMPLE, _DUO]
    with_dims = [replace(p, dimensions=_DIMS) for p in products]

    items_before, skipped_before = map_products_batch(products)
    items_after, skipped_after = map_products_batch(with_dims)

    assert len(items_before) == 3  # 1 simple + 2 variantes del Duo
    assert items_after == items_before
    assert skipped_after == skipped_before
    assert [_hash_item(i) for i in items_after] == [
        _hash_item(i) for i in items_before
    ]
