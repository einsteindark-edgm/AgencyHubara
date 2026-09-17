"""Reversa de pago (error operativo) + estado de pago coherente con Medusa.

Caso real (pedido #32, 2026-09-17): el operador confirmó el pago por error y
luego hizo refund en Medusa Admin. El dashboard siguió mostrando "Pagado"
porque el flag `hubara_payment_confirmed` pisaba el `payment_status` real.

Dos comportamientos:
  1. `pay_status` respeta un refund hecho en Medusa DESPUÉS de confirmar.
  2. "Reversar pago" deja el pedido (y el chat) de nuevo como NO pagado.

Gotcha de Medusa v2 (`getLastPaymentStatus`): tras un refund, cualquier
captura nueva deja la orden en `partially_refunded` — por eso la regla usa
los timestamps de Hubara (confirmado vs reversado), no solo el enum.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
import respx
from httpx import Response

from src.platform.medusa.client import HttpMedusaClient
from src.platform.medusa.settings import MedusaSettings
from src.platform.orders.command_port import ReversePaymentCommand
from src.platform.orders.medusa_order_command import (
    MedusaOrderCommand,
    NoopOrderCommand,
    apply_payment_reversal_to_chat_metadata,
)
from src.platform.orders.medusa_order_query import (
    MedusaOrderQuery,
    resolve_pay_status,
)
from src.platform.orders.state import build_reverse_payment_patch

_BASE_URL = "http://medusa.test"
NOW = 1_758_100_000_000
SESSION = "wa_573001234567"


# ----------------------------------------------------------------------
# 1. pay_status
# ----------------------------------------------------------------------


class TestResolvePayStatus:
    def test_confirmed_flag_with_not_paid_medusa_is_paid(self) -> None:
        md = {"hubara_payment_confirmed": True, "hubara_payment_confirmed_at_ms": 10}
        assert resolve_pay_status(md, "not_paid") == "paid"

    def test_refund_in_medusa_after_confirm_is_not_paid(self) -> None:
        """Pedido #32: flag quedó en True pero Medusa ya reembolsó."""
        md = {"hubara_payment_confirmed": True, "hubara_payment_confirmed_at_ms": 10}
        assert resolve_pay_status(md, "refunded") == "refund"
        assert resolve_pay_status(md, "partially_refunded") == "refund"

    def test_reversed_by_operator_is_pending(self) -> None:
        md = {
            "hubara_payment_confirmed": False,
            "hubara_payment_confirmed_at_ms": 10,
            "hubara_payment_reversed_at_ms": 20,
        }
        assert resolve_pay_status(md, "refunded") == "pending"
        assert resolve_pay_status(md, "not_paid") == "pending"

    def test_reconfirmed_after_reversal_is_paid(self) -> None:
        """Medusa queda `partially_refunded` tras la captura nueva."""
        md = {
            "hubara_payment_confirmed": True,
            "hubara_payment_confirmed_at_ms": 30,
            "hubara_payment_reversed_at_ms": 20,
        }
        assert resolve_pay_status(md, "partially_refunded") == "paid"

    def test_legacy_without_hubara_flags_maps_medusa(self) -> None:
        assert resolve_pay_status({}, "captured") == "paid"
        assert resolve_pay_status({}, "refunded") == "refund"
        assert resolve_pay_status({}, "not_paid") == "pending"


@pytest.mark.asyncio
@respx.mock
async def test_order_list_shows_refund_when_medusa_refunded_after_confirm() -> None:
    settings = MedusaSettings(base_url=_BASE_URL, admin_token="sk")  # type: ignore[call-arg]
    client = HttpMedusaClient(base_url=_BASE_URL, admin_token="sk", timeout=5.0)
    order = {
        "id": "order_32",
        "display_id": 32,
        "status": "pending",
        "payment_status": "refunded",
        "fulfillment_status": "not_fulfilled",
        "currency_code": "cop",
        "created_at": "2026-09-16T14:00:00.000Z",
        "updated_at": "2026-09-16T14:00:00.000Z",
        "total": 50000,
        "metadata": {
            "hubara_stage": "preparing",
            "hubara_payment_confirmed": True,
            "hubara_payment_confirmed_at_ms": 10,
        },
        "items": [],
        "shipping_address": {"first_name": "Ana", "last_name": "P"},
        "customer": {"first_name": "Ana", "last_name": "P"},
    }
    respx.get(f"{_BASE_URL}/admin/orders").mock(
        return_value=Response(200, json={"orders": [order], "count": 1, "offset": 0, "limit": 50})
    )
    respx.get(f"{_BASE_URL}/admin/draft-orders").mock(
        return_value=Response(200, json={"draft_orders": [], "count": 0, "offset": 0, "limit": 50})
    )
    try:
        result = await MedusaOrderQuery(client, settings).list(limit=50, offset=0, include_drafts=True)
    finally:
        await client.aclose()
    assert result.orders[0].pay_status == "refund"


# ----------------------------------------------------------------------
# 2a. patch de metadata de la orden
# ----------------------------------------------------------------------


class TestReversePaymentPatch:
    def test_clears_flag_and_records_event(self) -> None:
        md = {
            "hubara_stage": "preparing",
            "hubara_payment_confirmed": True,
            "hubara_payment_confirmed_at_ms": 10,
            "hubara_stage_history": [{"from": "new", "to": "preparing", "at_ms": 5, "by": "h"}],
        }
        patch = build_reverse_payment_patch(md, by="edgm", reason="clic por error", now_ms=NOW)
        assert patch["hubara_payment_confirmed"] is False
        assert patch["hubara_payment_reversed_at_ms"] == NOW
        assert patch["hubara_payment_reversed_by"] == "edgm"
        assert patch["hubara_payment_reversed_reason"] == "clic por error"
        last = patch["hubara_stage_history"][-1]
        assert last["event"] == "payment_reversed"
        assert last["from"] == last["to"] == "preparing"
        assert last["note"] == "clic por error"
        assert len(patch["hubara_stage_history"]) == 2


# ----------------------------------------------------------------------
# 2b. chat metadata vuelve a "pago pendiente de verificar"
# ----------------------------------------------------------------------


def _confirmed_chat() -> dict[str, Any]:
    return {
        "tag": "COMPRA_EXITOSA",
        "motivo": "Pago verificado por edgm desde dashboard de orders",
        "active_route": "ventas",
        "escalation_reason": "PAYMENT_VERIFICATION_PENDING",
        "registered_order": {"success": True, "order_id": "order_32"},
        "status_history": [
            {"tag": "HUMANO", "motivo": "Escalado", "active_route": "humano", "timestamp": 1.0},
            {
                "tag": "COMPRA_EXITOSA",
                "motivo": "Pago verificado",
                "active_route": "ventas",
                "timestamp": 2.0,
                "source": "orders_confirm_payment",
            },
        ],
        "episodes": [
            {
                "episode_id": "ep_1",
                "order_id": "order_32",
                "closing_tag": "COMPRA_EXITOSA",
                "payment_confirmed_at_ms": 2000,
                "payment_confirmed_by": "edgm",
            }
        ],
        "capi_outbox": [
            {"event_id": "purchase_order_32", "event_name": "Purchase", "order_id": "order_32"},
            {"event_id": "lead_x", "event_name": "Lead", "order_id": None},
        ],
    }


class TestChatReversal:
    def test_chat_back_to_pending_payment(self) -> None:
        from src.plugins.chats.api.dashboard import _compute_pending_payment_order_id

        md = _confirmed_chat()
        assert _compute_pending_payment_order_id(md) is None

        changed = apply_payment_reversal_to_chat_metadata(
            md, now_ms=NOW, by="edgm", reason="error", order_id="order_32"
        )

        assert changed is True
        assert md["tag"] == "HUMANO"
        assert md["active_route"] == "humano"
        ep = md["episodes"][0]
        assert ep["closing_tag"] == "CONFIRMADO_PAGO_PENDIENTE"
        assert "payment_confirmed_at_ms" not in ep
        assert ep["payment_reversed_at_ms"] == NOW
        assert md["status_history"][-1]["source"] == "orders_reverse_payment"
        # El botón "Confirmar pago" del chat vuelve a aparecer.
        assert _compute_pending_payment_order_id(md) == "order_32"

    def test_drops_unsent_purchase_from_capi_outbox(self) -> None:
        md = _confirmed_chat()
        apply_payment_reversal_to_chat_metadata(md, now_ms=NOW, by=None, reason=None, order_id="order_32")
        assert [e["event_id"] for e in md["capi_outbox"]] == ["lead_x"]

    def test_idempotent_when_chat_not_confirmed(self) -> None:
        md = _confirmed_chat()
        md["tag"] = "HUMANO"
        md["episodes"][0]["closing_tag"] = "CONFIRMADO_PAGO_PENDIENTE"
        md["episodes"][0].pop("payment_confirmed_at_ms")
        before = json.dumps(md, sort_keys=True)
        assert apply_payment_reversal_to_chat_metadata(md, now_ms=NOW, by=None, reason=None, order_id="order_32") is False
        assert json.dumps(md, sort_keys=True) == before


# ----------------------------------------------------------------------
# 2c. command adapter
# ----------------------------------------------------------------------


@pytest.fixture
async def adapter():
    client = HttpMedusaClient(base_url=_BASE_URL, admin_token="sk_test", timeout=5.0)
    yield MedusaOrderCommand(client)
    await client.aclose()


def _order(payment_status: str, *, confirmed: bool = True, payments: list | None = None) -> dict:
    return {
        "order": {
            "id": "order_32",
            "status": "pending",
            "total": 50000,
            "payment_status": payment_status,
            "metadata": {
                "hubara_stage": "preparing",
                "hubara_payment_confirmed": confirmed,
                "hubara_payment_confirmed_at_ms": 10,
            },
            "payment_collections": [{"id": "pc_1", "payments": payments or []}],
        }
    }


def _patched() -> Response:
    return Response(
        200,
        json={"order": {"id": "order_32", "metadata": {"hubara_stage": "preparing", "hubara_payment_confirmed": False}}},
    )


@pytest.mark.asyncio
async def test_reverse_refunds_captured_payment_in_medusa_and_clears_flag(adapter) -> None:
    payments = [
        {"id": "pay_1", "amount": 50000, "captures": [{"amount": 50000}], "refunds": []},
        {"id": "pay_old", "amount": 50000, "captures": [{"amount": 50000}], "refunds": [{"amount": 50000}]},
    ]
    with respx.mock(base_url=_BASE_URL) as m:
        m.get("/admin/orders/order_32").mock(return_value=Response(200, json=_order("captured", payments=payments)))
        refund = m.post("/admin/payments/pay_1/refund").mock(
            return_value=Response(200, json={"payment": {"id": "pay_1"}})
        )
        patch = m.post("/admin/orders/order_32").mock(return_value=_patched())

        result = await adapter.reverse_payment(
            ReversePaymentCommand(order_id="order_32", reason="clic por error", by="edgm")
        )

    assert result.success is True
    assert refund.call_count == 1
    assert json.loads(refund.calls[0].request.content) == {"amount": 50000, "note": "clic por error"}
    body = json.loads(patch.calls[0].request.content)
    assert body["metadata"]["hubara_payment_confirmed"] is False
    assert body["metadata"]["hubara_stage_history"][-1]["event"] == "payment_reversed"


@pytest.mark.asyncio
async def test_reverse_when_already_refunded_in_medusa_only_clears_flag(adapter) -> None:
    """Pedido #32: el operador ya hizo refund en Medusa Admin."""
    payments = [{"id": "pay_1", "amount": 50000, "captures": [{"amount": 50000}], "refunds": [{"amount": 50000}]}]
    with respx.mock(base_url=_BASE_URL, assert_all_called=False) as m:
        m.get("/admin/orders/order_32").mock(return_value=Response(200, json=_order("refunded", payments=payments)))
        refund = m.post(url__regex=r"/admin/payments/.*/refund")
        patch = m.post("/admin/orders/order_32").mock(return_value=_patched())

        result = await adapter.reverse_payment(ReversePaymentCommand(order_id="order_32"))

    assert result.success is True
    assert refund.call_count == 0
    assert patch.call_count == 1


@pytest.mark.asyncio
async def test_reverse_rejects_order_without_payment(adapter) -> None:
    with respx.mock(base_url=_BASE_URL, assert_all_called=False) as m:
        m.get("/admin/orders/order_32").mock(
            return_value=Response(200, json=_order("not_paid", confirmed=False))
        )
        patch = m.post("/admin/orders/order_32")

        result = await adapter.reverse_payment(ReversePaymentCommand(order_id="order_32"))

    assert result.success is False
    assert result.error_detail is not None and result.error_detail.startswith("invalid_state:")
    assert patch.call_count == 0


@pytest.mark.asyncio
async def test_reverse_does_not_touch_flag_when_medusa_refund_fails(adapter) -> None:
    payments = [{"id": "pay_1", "amount": 50000, "captures": [{"amount": 50000}], "refunds": []}]
    with respx.mock(base_url=_BASE_URL, assert_all_called=False) as m:
        m.get("/admin/orders/order_32").mock(return_value=Response(200, json=_order("captured", payments=payments)))
        m.post("/admin/payments/pay_1/refund").mock(return_value=Response(400, json={"message": "nope"}))
        patch = m.post("/admin/orders/order_32")

        result = await adapter.reverse_payment(ReversePaymentCommand(order_id="order_32"))

    assert result.success is False
    assert patch.call_count == 0


@pytest.mark.asyncio
async def test_noop_reverse_is_unavailable() -> None:
    r = await NoopOrderCommand().reverse_payment(ReversePaymentCommand(order_id="x"))
    assert r.success is False
