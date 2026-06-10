"""Tests del cache display_id→backend_id (L-2) y su integración en adapters.

Guard de la lección L-2: la resolución de display_id por page-scan cuesta
2-10s POR PÁGINA en Railway (variabilidad del list endpoint, no del payload).
El cache la elimina para todo id ya visto:

  * `list()` del query adapter puebla el cache gratis (el operador SIEMPRE
    ve la lista antes de actuar sobre un pedido).
  * Ambos adapters (query + command) resuelven cache-first — el cache es
    compartido a nivel proceso.
  * El scan en miss pide `fields=id,display_id` (sin expands).
"""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from src.platform.medusa.client import HttpMedusaClient
from src.platform.medusa.settings import MedusaSettings
from src.platform.orders import display_id_cache
from src.platform.orders.medusa_order_command import MedusaOrderCommand
from src.platform.orders.medusa_order_query import MedusaOrderQuery

_BASE_URL = "http://medusa.test"

_ORDER_ROW = {
    "id": "order_01CACHE",
    "display_id": 6,
    "status": "pending",
    "payment_status": "not_paid",
    "fulfillment_status": "not_fulfilled",
    "email": "a@b.co",
    "currency_code": "cop",
    "created_at": "2026-06-01T10:00:00Z",
    "updated_at": "2026-06-01T10:00:00Z",
    "canceled_at": None,
    "total": 10000,
    "subtotal": 10000,
    "shipping_total": 0,
    "tax_total": 0,
    "discount_total": 0,
    "metadata": {},
    "items": [],
    "shipping_address": None,
    "billing_address": None,
    "customer": None,
    "sales_channel": None,
}


def _query_adapter(client: HttpMedusaClient) -> MedusaOrderQuery:
    settings = MedusaSettings(  # type: ignore[call-arg]
        base_url=_BASE_URL, admin_token="sk_test_abc"
    )
    return MedusaOrderQuery(client, settings)


@pytest.fixture
async def client():
    c = HttpMedusaClient(base_url=_BASE_URL, admin_token="sk_test_abc", timeout=5.0)
    yield c
    await c.aclose()


def test_put_get_clear_roundtrip() -> None:
    assert display_id_cache.get("6") is None
    display_id_cache.put("6", "order_01CACHE")
    assert display_id_cache.get("6") == "order_01CACHE"
    display_id_cache.clear()
    assert display_id_cache.get("6") is None


@respx.mock
async def test_resolve_miss_scans_light_then_caches(client: HttpMedusaClient) -> None:
    """Miss: escanea con fields=id,display_id; el resultado queda cacheado."""
    orders_route = respx.get(f"{_BASE_URL}/admin/orders").mock(
        return_value=Response(
            200, json={"orders": [{"id": "order_01CACHE", "display_id": 6}], "count": 1}
        )
    )
    respx.get(f"{_BASE_URL}/admin/draft-orders").mock(
        return_value=Response(200, json={"draft_orders": [], "count": 0})
    )
    adapter = _query_adapter(client)

    resolved = await adapter._resolve_display_id("6")
    assert resolved == "order_01CACHE"

    scan_request = orders_route.calls[0].request
    assert "fields=id%2Cdisplay_id" in str(scan_request.url)

    # Segunda resolución: cache hit — cero llamadas nuevas.
    calls_before = len(respx.calls)
    assert await adapter._resolve_display_id("6") == "order_01CACHE"
    assert len(respx.calls) == calls_before


@respx.mock
async def test_list_populates_cache_for_later_resolution(
    client: HttpMedusaClient,
) -> None:
    """list() puebla el cache: resolver después no dispara ningún scan."""
    respx.get(f"{_BASE_URL}/admin/orders").mock(
        return_value=Response(200, json={"orders": [_ORDER_ROW], "count": 1})
    )
    respx.get(f"{_BASE_URL}/admin/draft-orders").mock(
        return_value=Response(200, json={"draft_orders": [], "count": 0})
    )
    adapter = _query_adapter(client)

    await adapter.list(limit=50, offset=0)
    assert display_id_cache.get("6") == "order_01CACHE"

    calls_before = len(respx.calls)
    assert await adapter._resolve_display_id("6") == "order_01CACHE"
    assert len(respx.calls) == calls_before


async def test_command_adapter_resolves_from_shared_cache() -> None:
    """El command adapter lee el MISMO cache que puebla el query adapter.

    client=None: si el cache-first no funcionara, el scan explotaría con
    AttributeError — el test garantiza cero I/O en el hit.
    """
    display_id_cache.put("6", "order_01CACHE")
    command = MedusaOrderCommand(client=None)  # type: ignore[arg-type]
    assert await command._resolve_display_id("6") == "order_01CACHE"


def test_cache_caps_entries() -> None:
    for i in range(4096):
        display_id_cache.put(str(i), f"order_{i}")
    assert display_id_cache.get("0") == "order_0"
    # Entrada 4097 dispara el reset defensivo y re-puebla solo la nueva.
    display_id_cache.put("overflow", "order_OVERFLOW")
    assert display_id_cache.get("overflow") == "order_OVERFLOW"
    assert display_id_cache.get("0") is None
