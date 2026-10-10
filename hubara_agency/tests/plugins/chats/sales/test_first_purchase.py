"""¿El cliente ya compró antes? — la condición del cupón de bienvenida.

Caso del 2026-10-09: la página ofrece un 5 % en la primera compra. Cuenta un
pedido que esta conversación registró ANTES del episodio activo, si no se
canceló ni es de prueba (OrderFacts manda: el vault solo guarda el vínculo).
El pedido del episodio que está en curso es justamente su primera compra.
"""
from __future__ import annotations

import pytest

from src.plugins.chats.agent.sales.use_cases.first_purchase import (
    FirstPurchaseUnknown,
    bought_before,
    previous_order_ids,
)
from src.sdk.connectorkit import InMemoryOrderFacts, OrderFacts


def _facts(order_id: str, *, stage: str = "delivered", pay_status: str = "paid", is_test: bool = False) -> OrderFacts:
    return OrderFacts(
        order_id=order_id, display_id="31", total_cop=45000, currency_code="cop", pay_status=pay_status,
        stage=stage, customer="Cliente", is_draft=False, is_test=is_test,
    )


def _metadata(*, previous: str | None = "order_01OLD", current: str | None = None) -> dict:
    episodes = []
    if previous:
        episodes.append({"episode_id": "ep_001", "closed_at_ms": 1, "closing_tag": "COMPRA_EXITOSA", "order_id": previous})
    episodes.append({"episode_id": "ep_002", "closed_at_ms": None, **({"order_id": current} if current else {})})
    history = [{"order_id": oid, "provider": "medusa", "success": True, "ts_ms": 1} for oid in (previous, current) if oid]
    return {"episodes": episodes, "registered_orders_history": history}


def test_the_orders_of_earlier_episodes_are_the_previous_ones() -> None:
    assert previous_order_ids(_metadata(previous="order_01OLD", current="order_02NOW")) == ("order_01OLD",)
    assert previous_order_ids(_metadata(previous=None)) == ()
    assert previous_order_ids({}) == ()


def test_a_failed_registration_is_not_a_purchase() -> None:
    metadata = {"registered_orders_history": [{"order_id": "order_X", "success": False, "ts_ms": 1}]}

    assert previous_order_ids(metadata) == ()


@pytest.mark.asyncio
async def test_a_customer_without_previous_orders_has_not_bought() -> None:
    assert await bought_before(_metadata(previous=None), InMemoryOrderFacts()) is False


@pytest.mark.asyncio
async def test_a_previous_order_that_went_ahead_counts_even_if_unpaid() -> None:
    facts = InMemoryOrderFacts([_facts("order_01OLD", stage="new", pay_status="pending")])

    assert await bought_before(_metadata(), facts) is True


@pytest.mark.asyncio
async def test_a_cancelled_or_test_order_does_not_count() -> None:
    assert await bought_before(_metadata(), InMemoryOrderFacts([_facts("order_01OLD", stage="cancelled")])) is False
    assert await bought_before(_metadata(), InMemoryOrderFacts([_facts("order_01OLD", is_test=True)])) is False


@pytest.mark.asyncio
async def test_an_order_deleted_in_medusa_does_not_count() -> None:
    # Ni en `facts` ni en `unresolved`: Medusa confirmó que no existe.
    assert await bought_before(_metadata(), InMemoryOrderFacts([])) is False


@pytest.mark.asyncio
async def test_when_medusa_cannot_answer_it_is_unknown_not_a_guess() -> None:
    with pytest.raises(FirstPurchaseUnknown):
        await bought_before(_metadata(), InMemoryOrderFacts(available=False))


@pytest.mark.asyncio
async def test_without_order_facts_a_registered_order_counts() -> None:
    assert await bought_before(_metadata(), None) is True
    assert await bought_before(_metadata(previous=None), None) is False
