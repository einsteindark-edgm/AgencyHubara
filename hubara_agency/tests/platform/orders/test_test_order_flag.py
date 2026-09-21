"""Pedido de prueba — el operador marca un pedido como "prueba" y deja de contar.

Pedido del operador (2026-09-21): hay pedidos que no son ventas reales
(pruebas del flujo, del bot, de pagos). Deben seguir visibles pero NO sumar en
ninguna estadística (Órdenes, Ads, campañas, scoring) ni mandar eventos a Meta.

Decisión de diseño: la marca vive en la metadata de MEDUSA
(`hubara_test_order`), igual que `hubara_stage` o `hubara_payment_confirmed`.
`OrderFacts` solo la ESPEJA — no hay una segunda copia editable que se desfase
(gotcha 13 / pedido #31).
"""
from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from src.platform.medusa.client import HttpMedusaClient
from src.platform.orders.command_port import SetTestOrderCommand
from src.platform.orders.facts import OrderFacts, OrderFactsSnapshot
from src.platform.orders.medusa_order_command import (
    MedusaOrderCommand,
    NoopOrderCommand,
    apply_payment_confirmation_to_chat_metadata,
    apply_test_order_to_chat_metadata,
)
from src.platform.orders.state import META_KEY_TEST_ORDER, build_test_order_patch

_BASE_URL = "http://medusa.test"
NOW_MS = 1_758_400_000_000


# ----------------------------------------------------------------------
# Patch de metadata
# ----------------------------------------------------------------------


def test_marcar_como_prueba_escribe_la_marca_y_deja_rastro() -> None:
    patch = build_test_order_patch(
        {"hubara_stage": "preparing"}, is_test=True, by="edgm", now_ms=NOW_MS
    )
    assert patch[META_KEY_TEST_ORDER] is True
    assert patch["hubara_stage_history"][-1] == {
        "from": "preparing",
        "to": "preparing",
        "at_ms": NOW_MS,
        "by": "edgm",
        "event": "test_order_marked",
    }


def test_desmarcar_apaga_la_marca_con_false_no_borrandola() -> None:
    # El merge-patch de Medusa conserva las keys ausentes: hay que escribir False.
    patch = build_test_order_patch(
        {META_KEY_TEST_ORDER: True}, is_test=False, by="edgm", now_ms=NOW_MS
    )
    assert patch[META_KEY_TEST_ORDER] is False
    assert patch["hubara_stage_history"][-1]["event"] == "test_order_unmarked"


@pytest.mark.parametrize(
    ("metadata", "is_test"),
    [({META_KEY_TEST_ORDER: True}, True), ({}, False), ({META_KEY_TEST_ORDER: False}, False)],
)
def test_marca_idempotente(metadata: dict, is_test: bool) -> None:
    assert build_test_order_patch(metadata, is_test=is_test, now_ms=NOW_MS) == {}


# ----------------------------------------------------------------------
# OrderFacts: la marca saca al pedido de toda estadística
# ----------------------------------------------------------------------


def _fact(**kw) -> OrderFacts:
    base = dict(
        order_id="order_1", display_id="#1", total_cop=90000, currency_code="COP",
        pay_status="paid", stage="delivered", customer="Ana", is_draft=False,
    )
    base.update(kw)
    return OrderFacts(**base)


def test_pedido_de_prueba_pagado_no_cuenta_como_ingreso() -> None:
    assert _fact().counts_as_revenue is True
    assert _fact(is_test=True).counts_as_revenue is False


def test_revenue_cop_ignora_pedido_de_prueba() -> None:
    snap = OrderFactsSnapshot(facts={"order_1": _fact(is_test=True)})
    assert snap.revenue_cop("order_1", frozen_total=90000) is None


# ----------------------------------------------------------------------
# Lectura: Medusa metadata → OrderSummaryDTO.is_test
# ----------------------------------------------------------------------


def _raw_order(metadata: dict) -> dict:
    return {
        "id": "order_01T",
        "display_id": 7,
        "status": "pending",
        "payment_status": "captured",
        "fulfillment_status": "not_fulfilled",
        "currency_code": "cop",
        "created_at": "2026-09-20T14:00:00.000Z",
        "updated_at": "2026-09-20T14:00:00.000Z",
        "total": 50000,
        "metadata": metadata,
        "items": [],
        "shipping_address": {"first_name": "Ana"},
        "customer": {"first_name": "Ana"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(("metadata", "expected"), [({META_KEY_TEST_ORDER: True}, True), ({}, False)])
async def test_listado_expone_la_marca_de_prueba(metadata: dict, expected: bool) -> None:
    from src.platform.medusa.settings import MedusaSettings
    from src.platform.orders.medusa_order_query import MedusaOrderQuery

    settings = MedusaSettings(base_url=_BASE_URL, admin_token="sk")  # type: ignore[call-arg]
    client = HttpMedusaClient(base_url=_BASE_URL, admin_token="sk", timeout=5.0)
    with respx.mock(base_url=_BASE_URL) as mock:
        mock.get("/admin/orders").mock(return_value=Response(200, json={
            "orders": [_raw_order(metadata)], "count": 1, "offset": 0, "limit": 50,
        }))
        mock.get("/admin/draft-orders").mock(return_value=Response(200, json={
            "draft_orders": [], "count": 0, "offset": 0, "limit": 50,
        }))
        result = await MedusaOrderQuery(client, settings).list()
    await client.aclose()
    summary = result.orders[0]
    assert summary.is_test is expected
    assert OrderFacts.from_summary(summary).is_test is expected


# ----------------------------------------------------------------------
# Comando: set_test_order escribe en Medusa y limpia el outbox CAPI
# ----------------------------------------------------------------------


@pytest.fixture
async def adapter():
    client = HttpMedusaClient(base_url=_BASE_URL, admin_token="sk_test", timeout=5.0)
    yield MedusaOrderCommand(client)
    await client.aclose()


@pytest.mark.asyncio
@respx.mock(base_url=_BASE_URL)
async def test_set_test_order_patchea_metadata_en_medusa(respx_mock, adapter) -> None:
    order_id = "order_01T"
    respx_mock.get(f"/admin/orders/{order_id}").mock(
        return_value=Response(200, json={"order": {
            "id": order_id, "status": "pending", "metadata": {"hubara_stage": "new"},
        }})
    )
    post = respx_mock.post(f"/admin/orders/{order_id}").mock(
        return_value=Response(200, json={"order": {
            "id": order_id, "metadata": {"hubara_stage": "new", META_KEY_TEST_ORDER: True},
        }})
    )
    result = await adapter.set_test_order(
        SetTestOrderCommand(order_id=order_id, is_test=True, by="edgm")
    )
    assert result.success is True
    body = json.loads(post.calls[-1].request.content)
    assert body["metadata"][META_KEY_TEST_ORDER] is True


@pytest.mark.asyncio
async def test_noop_set_test_order_avisa_medusa_no_disponible() -> None:
    r = await NoopOrderCommand().set_test_order(SetTestOrderCommand(order_id="x", is_test=True))
    assert r.success is False
    assert "medusa_unavailable" in (r.error_detail or "")


# ----------------------------------------------------------------------
# Chat / CAPI: un pedido de prueba no le manda nada a Meta
# ----------------------------------------------------------------------


def _chat_with_outbox() -> dict:
    return {
        "tag": "COMPRA_EXITOSA",
        "capi_outbox": [
            {"event_name": "Purchase", "order_id": "#7"},
            {"event_name": "OrderShipped", "order_id": "order_01T"},
            {"event_name": "Purchase", "order_id": "order_OTRO"},
            {"event_name": "LeadSubmitted", "order_id": None},
        ],
    }


def test_marcar_prueba_descarta_eventos_en_cola_de_ese_pedido() -> None:
    chat = _chat_with_outbox()
    changed = apply_test_order_to_chat_metadata(
        chat, order_id="#7", order_aliases=("order_01T",)
    )
    assert changed is True
    assert chat["capi_outbox"] == [
        {"event_name": "Purchase", "order_id": "order_OTRO"},
        {"event_name": "LeadSubmitted", "order_id": None},
    ]


def test_marcar_prueba_sin_eventos_en_cola_es_noop() -> None:
    chat = {"capi_outbox": [{"event_name": "Purchase", "order_id": "order_OTRO"}]}
    assert apply_test_order_to_chat_metadata(chat, order_id="#7") is False


def test_confirmar_pago_de_pedido_de_prueba_no_encola_purchase() -> None:
    chat = {
        "tag": "HUMANO",
        "registered_order": {"success": True, "order_id": "order_01T", "total_cop": 50000, "currency": "COP"},
        "episodes": [{"closing_tag": "CONFIRMADO_PAGO_PENDIENTE", "order_id": "order_01T"}],
        "ctwa_referrals": [{"ctwa_clid": "CLID", "captured_at_ms": NOW_MS - 1}],
    }
    changed = apply_payment_confirmation_to_chat_metadata(
        chat, now_ms=NOW_MS, session_id="wa_573001234567", order_id="order_01T",
        is_test_order=True,
    )
    assert changed is True
    assert chat["tag"] == "COMPRA_EXITOSA"
    assert not [e for e in chat.get("capi_outbox") or [] if e.get("event_name") == "Purchase"]
