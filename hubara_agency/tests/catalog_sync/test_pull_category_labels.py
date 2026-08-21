"""El pull guarda el NOMBRE real de la categoría, no solo el slug.

Sin esto el bot le muestra al cliente "Velas Aromaticas" (deslugificado, sin
tilde) en vez del nombre que el operador cargó en Medusa.
"""
from __future__ import annotations

import json

import pytest

from src.plugins.catalog.agent.contracts import CatalogSyncInput
from src.plugins.catalog.agent.use_cases.pull_catalog import PullCatalogUseCase


class _FakeClient:
    def __init__(self, products: list[dict]) -> None:
        self._products = products

    async def iter_products(self, **kwargs):
        for p in self._products:
            yield p


class _FakeService:
    def __init__(self, products: list[dict]) -> None:
        self.client = _FakeClient(products)


def _raw_product(categories: list[dict]) -> dict:
    return {
        "id": "p1",
        "title": "Vela Sagrado Corazón",
        "handle": "vela-sagrado-corazon",
        "status": "published",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "variants": [],
        "images": [],
        "tags": [],
        "categories": categories,
    }


@pytest.mark.asyncio
async def test_pull_maps_category_slug_to_real_name():
    svc = _FakeService(
        [
            _raw_product(
                [{"id": "pcat_1", "name": "Velas Aromáticas", "handle": "velas-aromaticas"}]
            )
        ]
    )
    result = await PullCatalogUseCase(medusa_service=svc).execute(CatalogSyncInput())
    product = json.loads(result.products_json)[0]

    assert product["categories"] == ["velas-aromaticas"]
    assert product["category_labels"] == {"velas-aromaticas": "Velas Aromáticas"}


@pytest.mark.asyncio
async def test_pull_without_categories_leaves_labels_empty():
    svc = _FakeService([_raw_product([])])
    result = await PullCatalogUseCase(medusa_service=svc).execute(CatalogSyncInput())
    assert json.loads(result.products_json)[0]["category_labels"] is None
