"""El worker de Sales arma las tools de cupones con los pedidos de la PERSONA.

Caso del 2026-10-09: el cupón de bienvenida es «para nuevos clientes en su
primer pedido». `apply_coupon` y `list_promotions` lo deciden con lo que
registró la conversación Y con todos los pedidos del número en Medusa
(`get_customer_orders_port`). Las tools se arman desde el registro del worker,
como en la activity (guard de «worker lambda missing import»).
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

SESSION = "wa_573001112233"


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.setenv("MEDUSA_BASE_URL", "http://medusa.test")
    monkeypatch.setenv("MEDUSA_ADMIN_TOKEN", "dummy")
    import src.plugins.chats.workers.sales as sales_worker
    from src.sdk.connectorkit import (
        FakePromotionsPort,
        InMemoryCustomerOrders,
        OrderFacts,
        PromotionDTO,
    )

    welcome = PromotionDTO(
        id="promo_bienvenida", code="BIENVENIDA", discount_type="percentage", value=5, currency_code="cop",
        target_type="items", allocation="across", max_quantity=None, product_ids=(), variant_ids=(),
        collection_ids=(), min_subtotal_cop=None, is_automatic=False, status="active", starts_at_ms=None,
        ends_at_ms=None, budget_type=None, budget_limit=None, budget_used=None,
        description="Descuento de bienvenida",
    )
    orders = InMemoryCustomerOrders({SESSION: [OrderFacts(
        order_id="order_09WEB", display_id="#12", total_cop=60000, currency_code="cop", pay_status="paid",
        stage="delivered", customer="Cliente", is_draft=False,
    )]})
    monkeypatch.setattr(sales_worker, "_promotions", FakePromotionsPort([replace(welcome, first_purchase_only=True)]))
    # raising=False: si el worker deja de leer este puerto, falla la aserción.
    monkeypatch.setattr(sales_worker, "get_customer_orders_port", lambda: orders, raising=False)
    return orders


def _tool(name: str, workspace: Path):
    from src.platform.tool_extensions import _EXTENSIONS  # type: ignore

    factory = dict(_EXTENSIONS).get(f"sales.{name}")
    assert factory is not None, f"sales.{name} no está registrada"
    return factory(workspace)


@pytest.mark.asyncio
async def test_apply_coupon_of_the_worker_looks_at_every_order_of_the_person(worker, tmp_path: Path) -> None:
    ctx = ToolContext(session_key=SESSION, channel="whatsapp", chat_id=SESSION)

    env = json.loads(await _tool("apply_coupon", tmp_path).execute_with_context(ctx, code="BIENVENIDA"))

    assert (env["applied"], env["reason"]) == (False, "first_purchase_only")
    assert worker.calls == [SESSION]


@pytest.mark.asyncio
async def test_list_promotions_of_the_worker_does_not_offer_it_to_a_returning_person(worker, tmp_path: Path) -> None:
    ctx = ToolContext(session_key=SESSION, channel="whatsapp", chat_id=SESSION)

    env = json.loads(await _tool("list_promotions", tmp_path).execute_with_context(ctx))

    assert env["promotions"][0]["applies_to_customer"] is False
    assert worker.calls == [SESSION]
