"""Envío gratis desde 6 cajas de la campaña en el pedido de Medusa (operador,
2026-10-20).

El registro llega con `shipping_cop=0` y la marca `free_shipping`: el borrador
lleva el envío en $0 con un nombre que lo dice (Medusa Admin y la guía) y la
marca en su metadata (`hubara_free_shipping`), que es de donde la leen Orders,
el cobro y el ETA sin importar el plugin de chats. El reintento de la
reconciliación registra la misma marca.
"""
from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from src.platform.orders.port import OrderShipping
from src.platform.orders.reconciliation import OUTCOME_RESOLVED, reconcile_one
from tests.platform.orders.test_medusa_order_registration import (  # noqa: F401 — `adapter` es fixture
    _BASE_URL,
    _ITEMS,
    _product_payload_for_handle,
    adapter,
)
from tests.platform.orders.test_reconciliation import KwargsPort, _failed_record, _write_metadata

FREE = {"campaign": "velitas-7-dic", "min_boxes": 6, "boxes": 6}


def _mock_medusa() -> respx.Route:
    respx.get(f"{_BASE_URL}/admin/products").mock(
        return_value=Response(200, json=_product_payload_for_handle("cruz-de-vida"))
    )
    respx.get(f"{_BASE_URL}/admin/customers").mock(
        return_value=Response(200, json={"customers": [], "count": 0, "offset": 0, "limit": 1})
    )
    respx.post(f"{_BASE_URL}/admin/customers").mock(
        return_value=Response(200, json={"customer": {"id": "cus_new_003", "email": "wa+wa_113@hubara.local"}})
    )
    respx.get(f"{_BASE_URL}/admin/shipping-options").mock(
        return_value=Response(200, json={"shipping_options": [{"id": "so_std_01", "name": "Envío estándar"}],
                                         "count": 1, "offset": 0, "limit": 50})
    )
    return respx.post(f"{_BASE_URL}/admin/draft-orders").mock(
        return_value=Response(200, json={"draft_order": {"id": "draft_free_001", "status": "draft",
                                                          "items": [], "shipping_methods": []}})
    )


@pytest.mark.asyncio
@respx.mock
async def test_a_free_shipping_order_reaches_medusa_with_zero_shipping_and_its_mark(adapter) -> None:  # noqa: F811
    draft_route = _mock_medusa()
    shipping = OrderShipping(city="Medellín", neighborhood="Poblado", address="Calle 59b sur 38",
                             phone="3001234567", receiver_name="Ana Pérez")

    result = await adapter.register_order(
        session_key="wa_113", items=_ITEMS, shipping=shipping, payment_method="cash_on_delivery",
        subtotal_cop=17000, shipping_cop=0, total_cop=17000, free_shipping=FREE,
    )

    assert result.success is True, result
    body = json.loads(draft_route.calls[0].request.content)
    (method,) = body["shipping_methods"]
    assert (method["amount"], method["name"]) == (0, "Envío gratis (6 cajas o más)")
    assert body["metadata"]["hubara_free_shipping"] == FREE


@pytest.mark.asyncio
@respx.mock
async def test_an_order_with_shipping_has_no_free_shipping_mark(adapter) -> None:  # noqa: F811
    draft_route = _mock_medusa()
    shipping = OrderShipping(city="Bogotá", neighborhood="Chapinero", address="Calle 100 #15-20",
                             phone="3001234567", receiver_name="Ana Pérez")

    await adapter.register_order(
        session_key="wa_114", items=_ITEMS, shipping=shipping, payment_method="transfer",
        subtotal_cop=17000, shipping_cop=5000, total_cop=22000,
    )

    body = json.loads(draft_route.calls[0].request.content)
    assert body["shipping_methods"][0]["name"] == "Envío estándar"
    assert "hubara_free_shipping" not in body["metadata"]


@pytest.mark.asyncio
async def test_the_reconciliation_retry_keeps_the_free_shipping(tmp_path) -> None:
    record = _failed_record(
        "AUDIT-F",
        items=[{"handle": "velas-de-deseos-onduladas", "quantity": 6, "unit_price_cop": 22500}],
        subtotal_cop=135000, shipping_cop=0, total_cop=135000, free_shipping=FREE,
    )
    _write_metadata(tmp_path, "wa_10000000001", {"failed_order_registrations": [record]})
    port = KwargsPort()

    outcome = await reconcile_one(vault_dir=tmp_path, session_key="wa_10000000001", audit_id="AUDIT-F", port=port)

    assert outcome.outcome == OUTCOME_RESOLVED
    (call,) = port.calls
    assert (call["shipping_cop"], call["free_shipping"]) == (0, FREE)


# ── la lectura: lo que paga el cliente nunca suma el envío real ─────────────
#
# Al despachar, el operador escribe el costo real del envío (la app lo exige).
# Con envío gratis ese costo lo paga la tienda: el total del cliente, el cobro
# de contra entrega, OrderFacts/Ads y el ETA no lo suman; queda aparte.

from src.platform.orders.command_port import ConfirmPaymentCommand  # noqa: E402
from src.platform.orders.state import META_KEY_FREE_SHIPPING, META_KEY_SHIPPING_COST, effective_amounts  # noqa: E402
from tests.platform.orders.test_real_shipping_cost import _order, command, query  # noqa: E402,F401


def test_with_free_shipping_the_real_cost_is_never_added_to_the_customer_total() -> None:
    marked = {META_KEY_FREE_SHIPPING: FREE, META_KEY_SHIPPING_COST: 18_000}

    assert effective_amounts(total=135_000, shipping_total=0, metadata=marked) == (135_000, 0, True)
    assert effective_amounts(total=135_000, shipping_total=0, metadata={META_KEY_FREE_SHIPPING: FREE}) == (135_000, 0, True)


def _free_order(**metadata) -> dict:
    order = _order({META_KEY_FREE_SHIPPING: FREE, **metadata})
    order.update(total=135_000, subtotal=135_000, shipping_total=0,
                 items=[{"title": "Caja de Velas de deseos onduladas", "quantity": 6, "unit_price": 22_500,
                         "total": 135_000}])
    return order


@pytest.mark.asyncio
@respx.mock
async def test_the_summary_shows_free_shipping_and_the_store_cost_apart(query) -> None:  # noqa: F811
    from tests.platform.orders.test_real_shipping_cost import _BASE_URL as base

    respx.get(f"{base}/admin/orders/order_01SHIP").mock(
        return_value=Response(200, json={"order": _free_order(hubara_stage="shipping", **{META_KEY_SHIPPING_COST: 18_000})})
    )

    detail = await query.get("order_01SHIP")

    assert (detail.summary.total_cop, detail.summary.shipping_cop) == (135_000, 0)
    assert detail.summary.free_shipping is True
    assert detail.summary.store_shipping_cost_cop == 18_000


@pytest.mark.asyncio
async def test_confirm_payment_of_a_free_shipping_order_charges_only_the_products(command) -> None:  # noqa: F811
    from tests.platform.orders.test_real_shipping_cost import _BASE_URL as base

    with respx.mock(base_url=base) as mock:
        mock.get("/admin/orders/order_01SHIP").mock(return_value=Response(200, json={"order": {
            "id": "order_01SHIP", "status": "pending", "payment_status": "not_paid", "total": 135_000,
            "shipping_total": 0,
            "metadata": {"hubara_stage": "delivered", META_KEY_FREE_SHIPPING: FREE, META_KEY_SHIPPING_COST: 18_000},
        }}))
        pc = mock.post("/admin/payment-collections").mock(
            return_value=Response(200, json={"payment_collection": {"id": "pc_1"}}))
        mock.post("/admin/payment-collections/pc_1/mark-as-paid").mock(
            return_value=Response(200, json={"payment_collection": {"id": "pc_1"}}))
        mock.post("/admin/orders/order_01SHIP").mock(
            return_value=Response(200, json={"order": {"id": "order_01SHIP", "metadata": {}}}))

        result = await command.confirm_payment(ConfirmPaymentCommand(order_id="order_01SHIP"))

    assert result.success is True
    assert json.loads(pc.calls[0].request.content)["amount"] == 135_000

