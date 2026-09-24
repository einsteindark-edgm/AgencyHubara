"""`list_orders` / `list_draft_orders` filtran por fecha de creación (C-7).

Las ventas de un cupón se cuentan desde el inicio de su cupo: con
`created_gte` Medusa devuelve solo lo creado desde ahí
(`created_at[$gte]=<ISO>`), en vez de paginar toda la historia.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from src.platform.medusa.client import HttpMedusaClient

_BASE = "https://m.test"
_SINCE = "2026-09-22T05:00:00Z"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,key",
    [("list_orders", "/admin/orders", "orders"), ("list_draft_orders", "/admin/draft-orders", "draft_orders")],
)
async def test_created_gte_becomes_the_created_at_gte_filter(method, path, key) -> None:
    c = HttpMedusaClient(base_url=_BASE, admin_token="sk_x")
    with respx.mock(base_url=_BASE) as r:
        route = r.get(path).mock(return_value=httpx.Response(200, json={key: [], "count": 0}))
        await getattr(c, method)(limit=10, created_gte=_SINCE)
        await getattr(c, method)(limit=10)
    await c.aclose()

    filtered, unfiltered = (call.request.url.params for call in route.calls)
    assert filtered["created_at[$gte]"] == _SINCE
    assert "created_at[$gte]" not in unfiltered
