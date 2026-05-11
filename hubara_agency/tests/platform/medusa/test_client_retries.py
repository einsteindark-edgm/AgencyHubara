"""Retries con tenacity sobre TransportError."""
from __future__ import annotations

import httpx
import pytest
import respx

from src.platform.medusa.client import HttpMedusaClient


_OK_PAGE = httpx.Response(
    200,
    json={"products": [], "count": 0, "offset": 0, "limit": 50},
)


@pytest.mark.asyncio
async def test_retries_on_transport_error_then_succeeds():
    c = HttpMedusaClient(base_url="https://m.test", admin_token="sk_x")
    with respx.mock(base_url="https://m.test") as r:
        r.get("/admin/products").mock(
            side_effect=[
                httpx.ConnectError("boom"),
                httpx.ConnectError("boom"),
                _OK_PAGE,
            ]
        )
        result = await c.list_products(limit=50)
        assert result["count"] == 0
    await c.aclose()


@pytest.mark.asyncio
async def test_gives_up_after_three_attempts():
    c = HttpMedusaClient(base_url="https://m.test", admin_token="sk_x")
    with respx.mock(base_url="https://m.test") as r:
        r.get("/admin/products").mock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(httpx.ConnectError):
            await c.list_products(limit=50)
    await c.aclose()
