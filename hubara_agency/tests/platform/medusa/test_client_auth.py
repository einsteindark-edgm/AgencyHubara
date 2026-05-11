"""Auth header construction: Basic <base64(token + ':')> y Bearer <jwt>."""
from __future__ import annotations

import base64

import httpx
import pytest
import respx

from src.platform.medusa.client import HttpMedusaClient


_OK_PAGE = httpx.Response(
    200,
    json={"products": [], "count": 0, "offset": 0, "limit": 50},
)


@pytest.mark.asyncio
async def test_basic_auth_with_token_then_colon():
    c = HttpMedusaClient(base_url="https://m.test", admin_token="sk_xyz")
    expected = "Basic " + base64.b64encode(b"sk_xyz:").decode()
    with respx.mock(base_url="https://m.test") as r:
        route = r.get("/admin/products").mock(return_value=_OK_PAGE)
        await c.list_products(limit=50)
        assert route.calls[0].request.headers["Authorization"] == expected
    await c.aclose()


@pytest.mark.asyncio
async def test_bearer_auth_logs_in_first():
    c = HttpMedusaClient(
        base_url="https://m.test",
        admin_email="a@b.c",
        admin_password="pw",
    )
    with respx.mock(base_url="https://m.test") as r:
        login = r.post("/auth/user/emailpass").mock(
            return_value=httpx.Response(200, json={"token": "jwt_abc"})
        )
        products = r.get("/admin/products").mock(return_value=_OK_PAGE)
        await c.list_products(limit=50)
        assert login.called
        assert (
            products.calls[0].request.headers["Authorization"]
            == "Bearer jwt_abc"
        )
    await c.aclose()


@pytest.mark.asyncio
async def test_requires_token_or_email_password():
    with pytest.raises(ValueError):
        HttpMedusaClient(base_url="https://m.test")
