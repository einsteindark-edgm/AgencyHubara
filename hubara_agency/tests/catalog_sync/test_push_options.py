"""El push preserva `options` al deserializar `products_json`.

Bug de prod (2026-07-16, primer sync post-#178): pull y snapshot ya eran
variant-aware, pero `_dto_from_dict` del push — un TERCER sitio de
deserialización raw→DTO — reconstruía el producto SIN `options` → el mapper
veía `options=None` → publicaba UN item por producto en vez de un item por
variante (`creates: 10` en vez de 21, sin `item_group_id`).

El fix consolida la reconstrucción raw→DTO en `product_dto_from_raw`
(platform/catalog/dtos.py), compartida por el read-path del snapshot y el
push — un solo lugar que olvidar en vez de dos.
"""
from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from src.platform.catalog.dtos import (
    CatalogImageDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
)
from src.platform.meta_catalog.dtos import MetaBatchRequest, MetaBatchResult
from src.platform.meta_catalog.port import MetaCatalogPort
from src.plugins.catalog.agent.contracts import PushMetaCatalogInput
from src.plugins.catalog.agent.use_cases.push_meta_catalog import (
    PushMetaCatalogUseCase,
)

_DUO_V2 = CatalogProductDTO(
    id="prod_duo_v2",
    handle="duo-zodiacal",
    title="Duo Zodiacal",
    status="published",
    description="Set de dos velas del signo que elijas.",
    thumbnail="https://assets.hubara.com.co/00-portada-01KXM9VC634R9TDA3PQ20T6G10.webp",
    options={"Signo": ["Leo", "Escorpion"]},
    variants=[
        CatalogVariantDTO(
            id="v_leo",
            title="Leo",
            options={"Signo": "Leo"},
            prices=[CatalogPriceDTO(amount="35000", currency_code="cop")],
        ),
        CatalogVariantDTO(
            id="v_esc",
            title="Escorpion",
            options={"Signo": "Escorpion"},
            prices=[CatalogPriceDTO(amount="35000", currency_code="cop")],
        ),
    ],
    images=[
        CatalogImageDTO(
            url="https://assets.hubara.com.co/00-portada-01KXM9VC634R9TDA3PQ20T6G10.webp",
            rank=0,
        ),
        CatalogImageDTO(
            url="https://assets.hubara.com.co/Leo-01KXM9VDBFR9ZAGZ5JRQA1TN02.webp",
            rank=1,
        ),
        CatalogImageDTO(
            url="https://assets.hubara.com.co/Escorpion-01KXM9VEE02SP6CSZE04JEC579.webp",
            rank=2,
        ),
    ],
)


class _FakeMetaPort(MetaCatalogPort):
    name = "fake_meta_port"

    def __init__(self) -> None:
        self.batches: list[MetaBatchRequest] = []

    async def upsert_batch(self, request: MetaBatchRequest) -> MetaBatchResult:
        self.batches.append(request)
        return MetaBatchResult(
            handle="h_test",
            ok=True,
            submitted=(
                len(request.creates)
                + len(request.updates)
                + len(request.deletes)
            ),
        )


def test_hash_detects_variant_axis_change():
    """El delta del push saltea items cuyo hash no cambió. Si el hash NO
    incluye `additional_variant_attribute` (o `item_group_id`), agregar el
    eje de variante a items ya publicados da "sin cambios" y el campo jamás
    llega a Meta (misma clase de bug que el TERCER parser de #179)."""
    from dataclasses import replace

    from src.platform.meta_catalog.dtos import MetaCatalogItem
    from src.plugins.catalog.agent.use_cases.push_meta_catalog import (
        _hash_item,
    )

    base = MetaCatalogItem(
        retailer_id="v_leo",
        name="Duo Zodiacal · Leo",
        description="desc",
        url="https://hubara.com.co/products/duo-zodiacal",
        image_url="https://img.example/leo.jpg",
        price="35000 COP",
        availability="in stock",
    )
    with_axis = replace(
        base,
        item_group_id="prod_duo_v2",
        additional_variant_attribute="Signo:Leo",
    )
    assert _hash_item(base) != _hash_item(with_axis)
    assert _hash_item(with_axis) != _hash_item(
        replace(with_axis, additional_variant_attribute="Signo:Tauro")
    )


@pytest.mark.asyncio
async def test_push_emits_per_variant_items_from_products_json():
    """El camino REAL del sync: products_json (asdict) → push → mapper."""
    port = _FakeMetaPort()
    use_case = PushMetaCatalogUseCase(meta_port=port)

    result = await use_case.execute(
        PushMetaCatalogInput(
            tenant_id="default",
            catalog_id="cat_1",
            system_user_token="tok",
            products_json=json.dumps([asdict(_DUO_V2)]),
            previous_meta_hashes_json="{}",
            last_meta_count=0,
        )
    )

    assert result.ok, result.error
    assert result.creates == 2, (
        f"esperaba 2 items per-variante, salieron {result.creates} — "
        "¿el push perdió options al deserializar?"
    )
    batch = port.batches[0]
    retailer_ids = {i.retailer_id for i in batch.creates}
    assert retailer_ids == {"v_leo", "v_esc"}
    assert all(i.item_group_id == "prod_duo_v2" for i in batch.creates)
