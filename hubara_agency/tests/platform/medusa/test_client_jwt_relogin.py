"""JWT mode: 401 → relogin → reintento. Secret mode: 401 → MedusaAPIError."""
from __future__ import annotations

import httpx
import pytest
import respx

from src.platform.medusa.client import HttpMedusaClient, MedusaAPIError


_OK_PAGE = httpx.Response(
    200,
    json={"products": [], "count": 0, "offset": 0, "limit": 50},
)


@pytest.mark.asyncio
async def test_jwt_mode_relogins_on_401():
    c = HttpMedusaClient(
        base_url="https://m.test",
        admin_email="a@b.c",
        admin_password="pw",
    )
    with respx.mock(base_url="https://m.test") as r:
        login = r.post("/auth/user/emailpass").mock(
            side_effect=[
                httpx.Response(200, json={"token": "jwt_old"}),
                httpx.Response(200, json={"token": "jwt_new"}),
            ]
        )
        products = r.get("/admin/products").mock(
            side_effect=[
                httpx.Response(401, json={"message": "expired"}),
                _OK_PAGE,
            ]
        )
        await c.list_products(limit=50)
        assert login.call_count == 2  # initial + relogin
        assert products.call_count == 2  # 401 then 200
        assert (
            products.calls[1].request.headers["Authorization"]
            == "Bearer jwt_new"
        )
    await c.aclose()


@pytest.mark.asyncio
async def test_secret_token_does_not_relogin_on_401():
    c = HttpMedusaClient(base_url="https://m.test", admin_token="sk_xyz")
    with respx.mock(base_url="https://m.test") as r:
        r.get("/admin/products").mock(
            return_value=httpx.Response(401, json={"message": "revoked"})
        )
        with pytest.raises(MedusaAPIError) as exc:
            await c.list_products(limit=50)
        assert exc.value.status_code == 401
    await c.aclose()
