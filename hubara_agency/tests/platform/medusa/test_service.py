"""MedusaProductService — wrapper tipado sobre HttpMedusaClient."""
from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
import respx

from src.platform.medusa.client import HttpMedusaClient
from src.platform.medusa.service import MedusaProductService


_PRODUCT_FIXTURE = {
    "id": "prod_1",
    "title": "Vela Lavanda",
    "handle": "vela-lavanda",
    "status": "published",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
    "variants": [
        {
            "id": "v1",
            "title": "Unica",
            "prices": [
                {"id": "pr1", "amount": 49.99, "currency_code": "usd"}
            ],
        }
    ],
}


@pytest.mark.asyncio
async def test_get_returns_typed_product():
    client = HttpMedusaClient(base_url="https://m.test", admin_token="sk_x")
    svc = MedusaProductService(client)
    with respx.mock(base_url="https://m.test") as r:
        r.get("/admin/products/prod_1").mock(
            return_value=httpx.Response(200, json={"product": _PRODUCT_FIXTURE})
        )
        product = await svc.get("prod_1")
        assert product.handle == "vela-lavanda"
        assert product.variants[0].prices[0].amount == Decimal("49.99")
    await client.aclose()


@pytest.mark.asyncio
async def test_list_returns_typed_page():
    client = HttpMedusaClient(base_url="https://m.test", admin_token="sk_x")
    svc = MedusaProductService(client)
    with respx.mock(base_url="https://m.test") as r:
        r.get("/admin/products").mock(
            return_value=httpx.Response(
                200,
                json={
                    "products": [_PRODUCT_FIXTURE],
                    "count": 1,
                    "offset": 0,
                    "limit": 50,
                },
            )
        )
        page = await svc.list(status="published", limit=50)
        assert page.count == 1
        assert page.products[0].handle == "vela-lavanda"
    await client.aclose()
