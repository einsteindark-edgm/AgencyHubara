"""iter_products: paginacion transparente, offset correcto."""
from __future__ import annotations

import httpx
import pytest
import respx

from src.platform.medusa.client import HttpMedusaClient


@pytest.mark.asyncio
async def test_iter_products_paginates_until_done():
    c = HttpMedusaClient(base_url="https://m.test", admin_token="sk_x")

    def _product(i: int) -> dict:
        return {
            "id": f"p{i}",
            "title": f"T{i}",
            "handle": f"h{i}",
            "status": "published",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }

    with respx.mock(base_url="https://m.test") as r:
        route = r.get("/admin/products").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "products": [_product(1), _product(2)],
                        "count": 5,
                        "offset": 0,
                        "limit": 2,
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "products": [_product(3), _product(4)],
                        "count": 5,
                        "offset": 2,
                        "limit": 2,
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "products": [_product(5)],
                        "count": 5,
                        "offset": 4,
                        "limit": 2,
                    },
                ),
            ]
        )

        collected: list[dict] = []
        async for p in c.iter_products(page_size=2):
            collected.append(p)

        assert [p["id"] for p in collected] == ["p1", "p2", "p3", "p4", "p5"]
        # 3 paginas
        assert route.call_count == 3
        # offset crece: 0, 2, 4
        offsets = [
            int(call.request.url.params["offset"]) for call in route.calls
        ]
        assert offsets == [0, 2, 4]
    await c.aclose()
