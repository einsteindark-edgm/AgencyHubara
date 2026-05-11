"""PullCatalogUseCase — mapeo Medusa→DTO con Decimal→str."""
from __future__ import annotations

import json

import pytest

from src.catalog_sync.contracts import CatalogSyncInput
from src.catalog_sync.use_cases.pull_catalog import PullCatalogUseCase


class _FakeClient:
    """Minimal stub para iter_products."""

    def __init__(self, products: list[dict]) -> None:
        self._products = products

    async def iter_products(self, **kwargs):
        for p in self._products:
            yield p


class _FakeService:
    def __init__(self, products: list[dict]) -> None:
        self.client = _FakeClient(products)


@pytest.mark.asyncio
async def test_pull_one_product_with_decimal_as_str():
    svc = _FakeService(
        [
            {
                "id": "p1",
                "title": "Vela Lavanda",
                "handle": "vela-lavanda",
                "status": "published",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "variants": [
                    {
                        "id": "v1",
                        "title": "u",
                        "prices": [
                            {
                                "id": "pr1",
                                "amount": 49.99,
                                "currency_code": "usd",
                            }
                        ],
                    }
                ],
                "images": [],
                "tags": [],
                "categories": [],
            }
        ]
    )
    use_case = PullCatalogUseCase(medusa_service=svc)
    result = await use_case.execute(CatalogSyncInput())

    assert result.count == 1
    payload = json.loads(result.products_json)
    assert payload[0]["handle"] == "vela-lavanda"
    # Critical: Decimal preservada como string (R-JSON-safe)
    assert payload[0]["variants"][0]["prices"][0]["amount"] == "49.99"
    assert isinstance(payload[0]["variants"][0]["prices"][0]["amount"], str)


@pytest.mark.asyncio
async def test_pull_empty_catalog():
    svc = _FakeService([])
    use_case = PullCatalogUseCase(medusa_service=svc)
    result = await use_case.execute(CatalogSyncInput())
    assert result.count == 0
    assert json.loads(result.products_json) == []


@pytest.mark.asyncio
async def test_tags_and_categories_flattened():
    svc = _FakeService(
        [
            {
                "id": "p1",
                "title": "X",
                "handle": "x",
                "status": "published",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "tags": [
                    {"id": "t1", "value": "Aroma: Lavanda"},
                    {"id": "t2", "value": "Color: Morado"},
                ],
                "categories": [
                    {"id": "c1", "name": "Velas", "handle": "velas"},
                    {"id": "c2", "name": "Aromaticas"},  # sin handle
                ],
                "variants": [],
                "images": [],
            }
        ]
    )
    use_case = PullCatalogUseCase(medusa_service=svc)
    result = await use_case.execute(CatalogSyncInput())
    payload = json.loads(result.products_json)
    assert payload[0]["tags"] == ["Aroma: Lavanda", "Color: Morado"]
    # Categoria con handle usa handle; sin handle cae a name
    assert payload[0]["categories"] == ["velas", "Aromaticas"]
