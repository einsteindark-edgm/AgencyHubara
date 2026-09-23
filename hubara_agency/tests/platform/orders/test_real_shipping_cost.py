"""Valor REAL del envío — lo fija el operador al marcar "en camino".

El envío que se cobra al registrar el pedido es una TARIFA MÍNIMA estimada
(la transportadora recalcula antes de despachar). Recién al pasar a "en
camino" se sabe cuánto cuesta: el operador escribe el valor real en el modal
y ese valor REEMPLAZA al estimado en todo Hubara.

Medusa v2 no deja editar el monto de un método de envío existente en una
orden real (las order-edits solo tocan envíos agregados en la edición), así
que el valor real vive en `metadata.hubara_shipping_cost_cop` — igual que la
etapa — y el query adapter (único writer de OrderFacts) lo aplica:

  * `shipping_cop` = envío real (o el estimado mientras no se confirme).
  * `total_cop`    = total de Medusa − envío estimado + envío real.
  * `shipping_confirmed` = True cuando el operador ya lo fijó.
"""
from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from src.platform.medusa.client import HttpMedusaClient
from src.platform.medusa.settings import MedusaSettings
from src.platform.orders.command_port import (
    ConfirmPaymentCommand,
    TransitionStageCommand,
)
from src.platform.orders.medusa_order_command import MedusaOrderCommand
from src.platform.orders.medusa_order_query import MedusaOrderQuery
from src.platform.orders.state import (
    META_KEY_SHIPPING_COST,
    effective_amounts,
    transition_stage,
)

_BASE_URL = "http://medusa.test"


# ── Regla pura ───────────────────────────────────────────────────────────


def test_without_real_shipping_the_estimate_stands():
    assert effective_amounts(
        total=57_900, shipping_total=7_900, metadata={}
    ) == (57_900, 7_900, False)


def test_real_shipping_replaces_the_estimate_in_the_total():
    assert effective_amounts(
        total=57_900,
        shipping_total=7_900,
        metadata={META_KEY_SHIPPING_COST: 12_000},
    ) == (62_000, 12_000, True)


@pytest.mark.parametrize("junk", [None, 0, -5, "12000", True, 1.5])
def test_junk_real_shipping_is_ignored(junk):
    assert effective_amounts(
        total=57_900, shipping_total=7_900, metadata={META_KEY_SHIPPING_COST: junk}
    ) == (57_900, 7_900, False)


def test_transition_to_shipping_records_the_real_shipping():
    patch = transition_stage(
        {"hubara_stage": "ready"},
        to_stage="shipping",
        by="human",
        shipping_cost_cop=12_000,
        now_ms=1,
    )
    assert patch["hubara_stage"] == "shipping"
    assert patch[META_KEY_SHIPPING_COST] == 12_000


def test_transition_without_shipping_cost_leaves_it_alone():
    patch = transition_stage(
        {"hubara_stage": "ready"}, to_stage="shipping", by="human", now_ms=1
    )
    assert META_KEY_SHIPPING_COST not in patch


# ── Lectura: summary, detalle y OrderFacts ven el valor real ─────────────


def _order(metadata: dict) -> dict:
    return {
        "id": "order_01SHIP",
        "display_id": 40,
        "status": "pending",
        "payment_status": "not_paid",
        "fulfillment_status": "not_fulfilled",
        "email": "wa+wa_1@hubara.local",
        "currency_code": "cop",
        "created_at": "2026-09-20T14:00:00.000Z",
        "updated_at": "2026-09-20T14:00:00.000Z",
        "total": 57_900,
        "subtotal": 50_000,
        "shipping_total": 7_900,
        "tax_total": 0,
        "discount_total": 0,
        "metadata": {"payment_method": "cash_on_delivery", **metadata},
        "items": [
            {"title": "Vela", "quantity": 1, "unit_price": 50_000, "total": 50_000}
        ],
        "shipping_address": {"first_name": "Ana", "last_name": "Ruiz", "city": "Cali"},
        "customer": {"first_name": "Ana", "last_name": "Ruiz"},
        "transactions": [],
        "fulfillments": [],
    }


@pytest.fixture
async def query():
    settings = MedusaSettings(base_url=_BASE_URL, admin_token="sk_test")  # type: ignore[call-arg]
    client = HttpMedusaClient(base_url=_BASE_URL, admin_token="sk_test", timeout=5.0)
    yield MedusaOrderQuery(client, settings)
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_summary_exposes_estimated_shipping_until_confirmed(query):
    respx.get(f"{_BASE_URL}/admin/orders/order_01SHIP").mock(
        return_value=Response(200, json={"order": _order({"hubara_stage": "ready"})})
    )
    detail = await query.get("order_01SHIP")
    assert detail is not None
    assert detail.summary.total_cop == 57_900
    assert detail.summary.shipping_cop == 7_900
    assert detail.summary.shipping_confirmed is False
    assert detail.shipping_cop == 7_900


@pytest.mark.asyncio
@respx.mock
async def test_real_shipping_rewrites_total_everywhere(query):
    respx.get(f"{_BASE_URL}/admin/orders/order_01SHIP").mock(
        return_value=Response(
            200,
            json={
                "order": _order(
                    {"hubara_stage": "shipping", META_KEY_SHIPPING_COST: 12_000}
                )
            },
        )
    )
    detail = await query.get("order_01SHIP")
    assert detail is not None
    assert detail.summary.total_cop == 62_000
    assert detail.summary.shipping_cop == 12_000
    assert detail.summary.shipping_confirmed is True
    assert detail.shipping_cop == 12_000
    assert detail.subtotal_cop == 50_000


# ── Comandos ─────────────────────────────────────────────────────────────


@pytest.fixture
async def command():
    client = HttpMedusaClient(base_url=_BASE_URL, admin_token="sk_test", timeout=5.0)
    yield MedusaOrderCommand(client)
    await client.aclose()


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_marking_shipping_persists_the_real_shipping(respx_mock, command):
    respx_mock.get("/admin/orders/order_01SHIP").mock(
        return_value=Response(
            200,
            json={"order": {"id": "order_01SHIP", "status": "pending",
                            "metadata": {"hubara_stage": "ready"}}},
        )
    )
    post = respx_mock.post("/admin/orders/order_01SHIP").mock(
        return_value=Response(
            200, json={"order": {"id": "order_01SHIP",
                                 "metadata": {"hubara_stage": "shipping"}}}
        )
    )

    result = await command.transition_stage(
        TransitionStageCommand(
            order_id="order_01SHIP", to_stage="shipping", shipping_cost_cop=12_000
        )
    )

    assert result.success is True
    body = json.loads(post.calls[0].request.content)
    assert body["metadata"][META_KEY_SHIPPING_COST] == 12_000


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_confirm_payment_charges_the_real_shipping(respx_mock, command):
    respx_mock.get("/admin/orders/order_01SHIP").mock(
        return_value=Response(
            200,
            json={
                "order": {
                    "id": "order_01SHIP",
                    "status": "pending",
                    "payment_status": "not_paid",
                    "total": 57_900,
                    "shipping_total": 7_900,
                    "metadata": {
                        "hubara_stage": "delivered",
                        META_KEY_SHIPPING_COST: 12_000,
                    },
                }
            },
        )
    )
    pc = respx_mock.post("/admin/payment-collections").mock(
        return_value=Response(200, json={"payment_collection": {"id": "pc_1"}})
    )
    respx_mock.post("/admin/payment-collections/pc_1/mark-as-paid").mock(
        return_value=Response(200, json={"payment_collection": {"id": "pc_1"}})
    )
    respx_mock.post("/admin/orders/order_01SHIP").mock(
        return_value=Response(
            200, json={"order": {"id": "order_01SHIP", "metadata": {}}}
        )
    )

    result = await command.confirm_payment(ConfirmPaymentCommand(order_id="order_01SHIP"))

    assert result.success is True
    assert json.loads(pc.calls[0].request.content)["amount"] == 62_000
