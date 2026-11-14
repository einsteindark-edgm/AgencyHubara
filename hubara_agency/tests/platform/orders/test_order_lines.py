"""Líneas de los pedidos para el reporte por producto (tarjeta del ciclo #49).

`MedusaOrderQuery.list_lines` sale de la MISMA llamada a la tienda que la
lista (no una por pedido) y arma cada línea con el MISMO armador que el
detalle, para que el reporte y el inspector no den dos cuentas distintas.
Datos inventados.
"""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from src.platform.medusa.client import HttpMedusaClient
from src.platform.medusa.settings import MedusaSettings
from src.platform.orders.medusa_order_query import MedusaOrderQuery

_BASE_URL = "http://medusa.test"


@pytest.fixture
async def adapter():
    settings = MedusaSettings(base_url=_BASE_URL, admin_token="sk_test_abc")  # type: ignore[call-arg]
    client = HttpMedusaClient(base_url=settings.base_url, admin_token=settings.admin_token, timeout=5.0)
    yield MedusaOrderQuery(client, settings)
    await client.aclose()


def _order(**meta) -> dict:
    return {
        "id": "order_01LINES",
        "display_id": 301,
        "status": "pending",
        "payment_status": "captured",
        "fulfillment_status": "not_fulfilled",
        "currency_code": "cop",
        "created_at": "2026-11-02T14:00:00.000Z",
        "updated_at": "2026-11-02T14:00:00.000Z",
        "total": 49000,
        "item_subtotal": 44000,
        "shipping_total": 5000,
        "metadata": {"payment_method": "payment_link", "source": "hubara_whatsapp_sales", **meta},
        "items": [
            {
                "id": "item_a",
                "title": "Vela Luna",
                "quantity": 2,
                "unit_price": 13500,
                "total": 27000,
                "product_id": "prod_luna",
                "metadata": {
                    "handle": "vela-luna",
                    "coupon_code": "LUNA10",
                    "list_unit_price_cop": 15000,
                    "discount_unit_cop": 1500,
                },
            },
            {
                "id": "item_b",
                "title": "Portavelas Sol",
                "quantity": 1,
                "unit_price": 17000,
                "total": 17000,
                "product_id": "prod_sol",
                "metadata": {"handle": "portavelas-sol", "color": "Dorado"},
            },
        ],
        "shipping_address": {"first_name": "Ana", "last_name": "Prueba", "city": "Bogotá"},
        "customer": {"first_name": "Ana", "last_name": "Prueba"},
    }


def _mock_store(orders: list[dict], drafts: list[dict] | None = None):
    orders_route = respx.get(f"{_BASE_URL}/admin/orders").mock(
        return_value=Response(200, json={"orders": orders, "count": len(orders), "offset": 0, "limit": 100})
    )
    drafts_route = respx.get(f"{_BASE_URL}/admin/draft-orders").mock(
        return_value=Response(
            200, json={"draft_orders": drafts or [], "count": len(drafts or []), "offset": 0, "limit": 100}
        )
    )
    return orders_route, drafts_route


@pytest.mark.asyncio
@respx.mock
async def test_lines_come_from_one_list_call_with_the_detail_builder(adapter):
    orders_route, drafts_route = _mock_store([_order()])

    result = await adapter.list_lines(limit=100, offset=0)

    assert orders_route.call_count == 1
    assert drafts_route.call_count == 1
    assert not respx.calls or all("/admin/orders/" not in str(c.request.url) for c in respx.calls)
    assert result.catalog_available is True
    assert result.error_detail is None
    [lines] = result.orders
    assert lines.display_id == "#301"
    assert lines.payment_method_label == "Link de pago"
    luna, sol = lines.items
    assert (luna.title, luna.quantity, luna.unit_price_cop, luna.total_cop) == ("Vela Luna", 2, 13500, 27000)
    assert luna.list_unit_price_cop == 15000
    assert luna.discount_unit_cop == 1500
    assert luna.coupon_code == "LUNA10"
    assert sol.color == "Dorado"
    assert sol.list_unit_price_cop is None


@pytest.mark.asyncio
@respx.mock
async def test_lines_match_the_order_detail(adapter):
    raw = _order()
    _mock_store([raw])
    respx.get(f"{_BASE_URL}/admin/orders/order_01LINES").mock(return_value=Response(200, json={"order": raw}))

    lines = (await adapter.list_lines(limit=100)).orders[0]
    detail = await adapter.get("order_01LINES")

    assert detail is not None
    assert lines.items == detail.items_detail
    assert lines.payment_method_label == detail.payment_method_label


@pytest.mark.asyncio
@respx.mock
async def test_price_adjustment_reason_reaches_the_line(adapter):
    raw = _order()
    raw["items"][1]["metadata"].update(
        {"price_adjusted_reason": "precio al por mayor", "list_unit_price_cop": 20000}
    )
    _mock_store([raw])

    sol = (await adapter.list_lines(limit=100)).orders[0].items[1]

    assert sol.price_adjusted_reason == "precio al por mayor"
    assert sol.list_unit_price_cop == 20000


@pytest.mark.asyncio
@respx.mock
async def test_drafts_bring_their_lines_too(adapter):
    draft = _order()
    draft["id"] = "order_01DRAFT"
    draft["display_id"] = 302
    _mock_store([], [draft])

    result = await adapter.list_lines(limit=100)

    assert [o.display_id for o in result.orders] == ["#302"]
    assert len(result.orders[0].items) == 2


@pytest.mark.asyncio
@respx.mock
async def test_store_failure_answers_empty_with_the_reason(adapter):
    respx.get(f"{_BASE_URL}/admin/orders").mock(return_value=Response(503, text="Service Unavailable"))
    respx.get(f"{_BASE_URL}/admin/draft-orders").mock(return_value=Response(503, text="Service Unavailable"))

    result = await adapter.list_lines(limit=100)

    assert result.orders == []
    assert result.catalog_available is False
    assert "503" in (result.error_detail or "")
