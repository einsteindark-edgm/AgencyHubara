"""`MedusaOrderCommand.adjust_prices` — el precio negociado queda en Medusa.

Pedido #169 (2026-11-09): vendido al por mayor con precio negociado, quedó a
precio de lista, cobrado ($475.000 en Medusa) y entregado. El ajuste:

  * edita las líneas en Medusa (orden real → `order-edits`; borrador →
    `draft-orders/{id}/edit`), con el precio de lista y el motivo en la línea;
  * si el pedido ya tenía el cobro registrado por más del nuevo total,
    reembolsa la diferencia en Medusa (no mueve dinero: corrige el cobro);
  * deja la entrada `price_adjusted` en el historial del pedido.

Medusa 2.22.1 (verificado en el código fuente): `POST /admin/order-edits`
(begin) → `POST /admin/order-edits/{order_id}/items/item/{item_id}`
(`quantity`, `unit_price`, `metadata`) → `POST /admin/order-edits/{order_id}/confirm`
(acepta el cambio en PENDING); cancelar = `DELETE /admin/order-edits/{order_id}`.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
import respx
from httpx import Response

from src.platform.medusa.client import HttpMedusaClient
from src.platform.orders.command_port import AdjustPricesCommand, LinePrice
from src.platform.orders.medusa_order_command import MedusaOrderCommand, NoopOrderCommand

_BASE = "http://medusa.test"
OID = "order_169"


@pytest.fixture
async def adapter():
    client = HttpMedusaClient(base_url=_BASE, admin_token="sk_test", timeout=5.0)
    yield MedusaOrderCommand(client)
    await client.aclose()


def _order(
    *,
    status: str = "pending",
    prices: tuple[int, int] = (22500, 25000),
    captured: int = 0,
    refunded: int = 0,
    stage: str = "delivered",
    extra_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ond, cua = prices
    payments = (
        [{
            "id": "pay_1",
            "captures": [{"amount": captured}],
            "refunds": [{"amount": refunded}] if refunded else [],
        }]
        if captured
        else []
    )
    meta: dict[str, Any] = {
        "hubara_stage": stage,
        "hubara_free_shipping": {"boxes": 20, "campaign": "velitas-7-dic", "min_boxes": 6},
        "hubara_stage_history": [{"from": "shipping", "to": stage, "at_ms": 1, "by": "human"}],
        **(extra_meta or {}),
    }
    if captured:
        meta.update({"hubara_payment_confirmed": True, "hubara_payment_confirmed_at_ms": 2})
    return {
        "id": OID,
        "status": status,
        "total": 10 * ond + 10 * cua,
        "shipping_total": 0,
        "payment_status": "captured" if captured else "not_paid",
        "metadata": meta,
        "items": [
            {"id": "ordli_ond", "title": "Caja de Velas de deseos onduladas", "quantity": 10,
             "unit_price": ond, "metadata": {"aroma": "Navidad"}},
            {"id": "ordli_cua", "title": "Caja de Velas de deseos cuadradas", "quantity": 10,
             "unit_price": cua, "metadata": {"aroma": "Navidad"}},
        ],
        "payment_collections": [{"id": "pc_1", "payments": payments}],
    }


def _cmd(*prices: tuple[str, int], reason: str = "Precio por mayor negociado") -> AdjustPricesCommand:
    return AdjustPricesCommand(
        order_id=OID,
        prices=tuple(LinePrice(item_id=i, unit_price_cop=p) for i, p in prices),
        reason=reason,
        by="edgm",
    )


def _ok(key: str = "order_preview") -> Response:
    return Response(200, json={key: {"id": OID}})


@pytest.mark.asyncio
async def test_unpaid_order_edits_lines_in_medusa_and_records_history(adapter) -> None:
    with respx.mock(base_url=_BASE, assert_all_called=False) as m:
        m.get(f"/admin/orders/{OID}").mock(side_effect=[
            Response(200, json={"order": _order()}),
            Response(200, json={"order": _order(prices=(18000, 20000))}),
            Response(200, json={"order": _order(prices=(18000, 20000))}),
        ])
        begin = m.post("/admin/order-edits").mock(return_value=_ok("order_change"))
        item_ond = m.post(f"/admin/order-edits/{OID}/items/item/ordli_ond").mock(return_value=_ok())
        item_cua = m.post(f"/admin/order-edits/{OID}/items/item/ordli_cua").mock(return_value=_ok())
        confirm = m.post(f"/admin/order-edits/{OID}/confirm").mock(return_value=_ok())
        refund = m.post(url__regex=r"/admin/payments/.*/refund")
        patch = m.post(f"/admin/orders/{OID}").mock(return_value=Response(200, json={"order": {"id": OID}}))

        result = await adapter.adjust_prices(_cmd(("ordli_ond", 18000), ("ordli_cua", 20000)))

    assert result.success is True, result.error_detail
    assert json.loads(begin.calls[0].request.content)["order_id"] == OID
    body = json.loads(item_ond.calls[0].request.content)
    assert body["quantity"] == 10
    assert body["unit_price"] == 18000
    assert body["metadata"]["list_unit_price_cop"] == 22500
    assert body["metadata"]["price_adjusted_reason"] == "Precio por mayor negociado"
    assert json.loads(item_cua.calls[0].request.content)["unit_price"] == 20000
    assert confirm.call_count == 1
    assert refund.call_count == 0
    history = json.loads(patch.calls[0].request.content)["metadata"]["hubara_stage_history"]
    assert history[-1]["event"] == "price_adjusted"
    assert history[-1]["total_before_cop"] == 475000
    assert history[-1]["total_after_cop"] == 380000
    assert history[-1]["by"] == "edgm"


@pytest.mark.asyncio
async def test_paid_order_refunds_the_difference_and_stays_paid(adapter) -> None:
    """El 169: cobrado por $475.000, el precio real da $380.000 → se registra
    en Medusa el reembolso de $95.000 con el motivo; queda la marca."""
    with respx.mock(base_url=_BASE, assert_all_called=False) as m:
        m.get(f"/admin/orders/{OID}").mock(side_effect=[
            Response(200, json={"order": _order(captured=475000)}),
            Response(200, json={"order": _order(prices=(18000, 20000), captured=475000)}),
            Response(200, json={"order": _order(prices=(18000, 20000), captured=475000)}),
        ])
        m.post("/admin/order-edits").mock(return_value=_ok("order_change"))
        m.post(url__regex=rf"/admin/order-edits/{OID}/items/item/.*").mock(return_value=_ok())
        m.post(f"/admin/order-edits/{OID}/confirm").mock(return_value=_ok())
        refund = m.post("/admin/payments/pay_1/refund").mock(
            return_value=Response(200, json={"payment": {"id": "pay_1"}})
        )
        patch = m.post(f"/admin/orders/{OID}").mock(return_value=Response(200, json={"order": {"id": OID}}))

        result = await adapter.adjust_prices(_cmd(("ordli_ond", 18000), ("ordli_cua", 20000)))

    assert result.success is True, result.error_detail
    refund_body = json.loads(refund.calls[0].request.content)
    assert refund_body["amount"] == 95000
    assert "Precio por mayor negociado" in refund_body["note"]
    meta = json.loads(patch.calls[0].request.content)["metadata"]
    assert meta["hubara_price_adjustment_refunded_cop"] == 95000
    assert meta["hubara_stage_history"][-1]["refunded_cop"] == 95000


@pytest.mark.asyncio
async def test_retry_after_failed_refund_only_refunds(adapter) -> None:
    """La edición ya quedó en Medusa pero el reembolso falló: reintentar con
    los mismos precios no edita de nuevo, solo reembolsa lo que sobra."""
    already = _order(prices=(18000, 20000), captured=475000)
    with respx.mock(base_url=_BASE, assert_all_called=False) as m:
        m.get(f"/admin/orders/{OID}").mock(return_value=Response(200, json={"order": already}))
        begin = m.post("/admin/order-edits")
        refund = m.post("/admin/payments/pay_1/refund").mock(
            return_value=Response(200, json={"payment": {"id": "pay_1"}})
        )
        m.post(f"/admin/orders/{OID}").mock(return_value=Response(200, json={"order": {"id": OID}}))

        result = await adapter.adjust_prices(_cmd(("ordli_ond", 18000), ("ordli_cua", 20000)))

    assert result.success is True, result.error_detail
    assert begin.call_count == 0
    assert json.loads(refund.calls[0].request.content)["amount"] == 95000


@pytest.mark.asyncio
async def test_nothing_to_change_is_a_validation_error(adapter) -> None:
    with respx.mock(base_url=_BASE, assert_all_called=False) as m:
        m.get(f"/admin/orders/{OID}").mock(return_value=Response(200, json={"order": _order()}))
        patch = m.post(f"/admin/orders/{OID}")

        result = await adapter.adjust_prices(_cmd(("ordli_ond", 22500)))

    assert result.success is False
    assert result.error_detail is not None and result.error_detail.startswith("validation_error:")
    assert patch.call_count == 0


@pytest.mark.asyncio
async def test_raising_the_price_of_a_paid_order_is_rejected(adapter) -> None:
    with respx.mock(base_url=_BASE, assert_all_called=False) as m:
        m.get(f"/admin/orders/{OID}").mock(
            return_value=Response(200, json={"order": _order(captured=475000)})
        )
        begin = m.post("/admin/order-edits")

        result = await adapter.adjust_prices(_cmd(("ordli_ond", 30000)))

    assert result.success is False
    assert result.error_detail is not None and "reversa el pago" in result.error_detail
    assert begin.call_count == 0


@pytest.mark.asyncio
async def test_failure_mid_edit_cancels_the_edit_and_writes_nothing(adapter) -> None:
    with respx.mock(base_url=_BASE, assert_all_called=False) as m:
        m.get(f"/admin/orders/{OID}").mock(return_value=Response(200, json={"order": _order()}))
        m.post("/admin/order-edits").mock(return_value=_ok("order_change"))
        m.post(f"/admin/order-edits/{OID}/items/item/ordli_ond").mock(
            return_value=Response(400, json={"message": "nope"})
        )
        cancel = m.delete(f"/admin/order-edits/{OID}").mock(return_value=Response(200, json={}))
        confirm = m.post(f"/admin/order-edits/{OID}/confirm")
        patch = m.post(f"/admin/orders/{OID}")

        result = await adapter.adjust_prices(_cmd(("ordli_ond", 18000)))

    assert result.success is False
    assert cancel.call_count == 1
    assert confirm.call_count == 0
    assert patch.call_count == 0


@pytest.mark.asyncio
async def test_draft_order_uses_the_draft_edit(adapter) -> None:
    draft = _order(status="draft", stage="new")
    edited = _order(status="draft", stage="new", prices=(18000, 25000))
    with respx.mock(base_url=_BASE, assert_all_called=False) as m:
        m.get(f"/admin/orders/{OID}").mock(side_effect=[
            Response(200, json={"order": draft}),
            Response(200, json={"order": edited}),
        ])
        m.get(f"/admin/draft-orders/{OID}").mock(return_value=Response(200, json={"draft_order": edited}))
        begin = m.post(f"/admin/draft-orders/{OID}/edit").mock(return_value=_ok("draft_order_preview"))
        item = m.post(f"/admin/draft-orders/{OID}/edit/items/item/ordli_ond").mock(
            return_value=_ok("draft_order_preview")
        )
        confirm = m.post(f"/admin/draft-orders/{OID}/edit/confirm").mock(return_value=_ok("draft_order"))
        order_edit = m.post("/admin/order-edits")
        patch = m.post(f"/admin/draft-orders/{OID}").mock(
            return_value=Response(200, json={"draft_order": {"id": OID}})
        )

        result = await adapter.adjust_prices(_cmd(("ordli_ond", 18000)))

    assert result.success is True, result.error_detail
    assert begin.call_count == 1
    assert json.loads(item.calls[0].request.content)["unit_price"] == 18000
    assert confirm.call_count == 1
    assert order_edit.call_count == 0
    assert patch.call_count == 1


@pytest.mark.asyncio
async def test_cancelled_order_cannot_be_adjusted(adapter) -> None:
    with respx.mock(base_url=_BASE, assert_all_called=False) as m:
        m.get(f"/admin/orders/{OID}").mock(
            return_value=Response(200, json={"order": _order(stage="cancelled")})
        )
        begin = m.post("/admin/order-edits")

        result = await adapter.adjust_prices(_cmd(("ordli_ond", 18000)))

    assert result.success is False
    assert result.error_detail is not None and result.error_detail.startswith("invalid_state:")
    assert begin.call_count == 0


@pytest.mark.asyncio
async def test_noop_adjust_is_unavailable() -> None:
    r = await NoopOrderCommand().adjust_prices(_cmd(("x", 1)))
    assert r.success is False


@pytest.mark.asyncio
async def test_edit_does_not_leave_the_pending_payment_collection_medusa_creates(adapter) -> None:
    """Medusa 2.22 (`confirmOrderEditRequestWorkflow` →
    `createOrUpdateOrderPaymentCollectionWorkflow`): confirmar la edición de un
    pedido sin cobrar CREA un cobro `not_paid` por el nuevo total. «Confirmar
    pago» de Hubara crea el suyo, así que Medusa quedaría con dos (uno pagado y
    otro pendiente). El ajuste borra el que creó la edición; uno que ya existía
    antes no se toca."""
    before = _order()
    before["payment_collections"].append({"id": "pc_old", "status": "not_paid", "payments": []})
    after = _order(prices=(18000, 25000))
    after["payment_collections"] += [
        {"id": "pc_old", "status": "not_paid", "payments": []},
        {"id": "pc_edit", "status": "not_paid", "payments": []},
    ]
    with respx.mock(base_url=_BASE, assert_all_called=False) as m:
        m.get(f"/admin/orders/{OID}").mock(side_effect=[
            Response(200, json={"order": before}),
            Response(200, json={"order": after}),
            Response(200, json={"order": after}),
        ])
        m.post("/admin/order-edits").mock(return_value=_ok("order_change"))
        m.post(url__regex=rf"/admin/order-edits/{OID}/items/item/.*").mock(return_value=_ok())
        m.post(f"/admin/order-edits/{OID}/confirm").mock(return_value=_ok())
        drop_new = m.delete("/admin/payment-collections/pc_edit").mock(
            return_value=Response(200, json={"id": "pc_edit", "deleted": True})
        )
        drop_old = m.delete("/admin/payment-collections/pc_old")
        m.post(f"/admin/orders/{OID}").mock(return_value=Response(200, json={"order": {"id": OID}}))

        result = await adapter.adjust_prices(_cmd(("ordli_ond", 18000)))

    assert result.success is True, result.error_detail
    assert drop_new.call_count == 1
    assert drop_old.call_count == 0
