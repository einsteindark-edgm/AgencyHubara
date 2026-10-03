"""OrderFacts — los datos de un pedido que TODO el dashboard lee de un solo lugar.

Caso real (pedido #31, 2026-09-17): el total se editó en Medusa; Orders mostró
el valor nuevo y Ads el viejo (lo leía de una copia congelada en el chat).
Ahora Orders y Ads leen el mismo `OrderFactsStore`: el `OrderQueryPort` que usa
Orders lo alimenta, y Ads lo consulta por id.

Contract suite: el adapter real (`OrderFactsStore` sobre un query port) y el
fake oficial (`InMemoryOrderFacts`) deben comportarse igual.
"""
from __future__ import annotations

import asyncio

import pytest

from src.platform.events.bus import DashboardEventBus
from src.platform.orders.facts import (
    InMemoryOrderFacts,
    OrderFacts,
    OrderFactsReadPort,
    OrderFactsStore,
    RecordingOrderQuery,
)
from src.platform.orders.query_port import OrderDetailDTO, OrderSummaryDTO
from tests.platform.orders.fakes import FakeQuery
from tests.platform.orders.fakes import order_summary as _summary


def _facts(s: OrderSummaryDTO) -> OrderFacts:
    return OrderFacts.from_summary(s)


PAID = _summary("order_31", 120000)
UNPAID = _summary("order_40", 50000, pay="pending")
CANCELLED = _summary("order_41", 70000, stage="cancelled")
REFUNDED = _summary("order_32", 50000, pay="refund")
ALL = [PAID, UNPAID, CANCELLED, REFUNDED]


def _real(available: bool = True) -> OrderFactsReadPort:
    return OrderFactsStore(FakeQuery(list(ALL), available=available), page_size=2)


def _fake(available: bool = True) -> OrderFactsReadPort:
    return InMemoryOrderFacts([_facts(o) for o in ALL], available=available)


@pytest.fixture(params=[_real, _fake], ids=["store", "in_memory"])
def make(request):
    return request.param


# ----------------------------------------------------------------------
# Contract suite
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contract_satisfies_protocol(make) -> None:
    assert isinstance(make(), OrderFactsReadPort)


@pytest.mark.asyncio
async def test_contract_paid_order_counts_as_revenue(make) -> None:
    snap = await make().get_facts(["order_31"])
    assert snap.facts["order_31"].total_cop == 120000
    assert snap.revenue_cop("order_31", frozen_total=90000) == 120000
    assert snap.stale is False


@pytest.mark.asyncio
@pytest.mark.parametrize("order_id", ["order_40", "order_41", "order_32"])
async def test_contract_unpaid_cancelled_or_refunded_is_not_revenue(make, order_id) -> None:
    snap = await make().get_facts([order_id])
    assert snap.revenue_cop(order_id, frozen_total=1) is None


@pytest.mark.asyncio
async def test_contract_order_missing_in_medusa_is_not_revenue(make) -> None:
    snap = await make().get_facts(["order_gone"])
    assert "order_gone" not in snap.unresolved
    assert snap.revenue_cop("order_gone", frozen_total=90000) is None


@pytest.mark.asyncio
async def test_contract_medusa_down_falls_back_to_frozen_and_flags_stale(make) -> None:
    snap = await make(available=False).get_facts(["order_31"])
    assert snap.unresolved == frozenset({"order_31"})
    assert snap.stale is True
    assert snap.revenue_cop("order_31", frozen_total=90000) == 90000


@pytest.mark.asyncio
async def test_contract_empty_request_is_fresh_and_empty(make) -> None:
    snap = await make().get_facts([])
    assert snap.facts == {} and snap.unresolved == frozenset() and snap.stale is False


# ----------------------------------------------------------------------
# OrderFactsStore — una sola variable para Orders y Ads
# ----------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.asyncio
async def test_orders_view_feeds_the_value_ads_reads() -> None:
    """#31: lo que lee Orders (vía el query port canónico) es lo que lee Ads."""
    query = FakeQuery(list(ALL))
    store = OrderFactsStore(query, page_size=2)
    orders_port = RecordingOrderQuery(query, store)

    await store.get_facts(["order_31"])
    query.edit("order_31", total_cop=150000)
    await orders_port.list(limit=100)  # el operador abre Orders
    calls = len(query.list_calls)

    snap = await store.get_facts(["order_31"])
    assert snap.facts["order_31"].total_cop == 150000
    assert len(query.list_calls) == calls  # no hizo falta volver a Medusa


@pytest.mark.asyncio
async def test_recording_query_records_detail_reads() -> None:
    query = FakeQuery([])
    store = OrderFactsStore(query)
    detail = OrderDetailDTO(
        summary=PAID, items_detail=[], shipping_address=None, billing_address=None,
        subtotal_cop=0, shipping_cop=0, tax_total_cop=0, discount_total_cop=0,
        timeline=[], payment_method_label=None,
    )

    async def _get(order_id: str) -> OrderDetailDTO:
        return detail

    query.get = _get  # type: ignore[method-assign]
    await RecordingOrderQuery(query, store).get("#31")
    snap = await store.get_facts(["order_31"])
    assert snap.facts["order_31"].total_cop == 120000
    assert query.list_calls == []


@pytest.mark.asyncio
async def test_orders_event_on_the_bus_invalidates() -> None:
    """Un cambio hecho desde el dashboard (confirmar/reversar pago, stage…)
    publica `orders` en el bus → el próximo read vuelve a Medusa."""
    query = FakeQuery(list(ALL))
    bus = DashboardEventBus()
    store = OrderFactsStore(query, page_size=2, bus=bus)
    await store.get_facts(["order_40"])
    query.edit("order_40", pay_status="paid")

    bus.publish("orders", "changed", id="#40")
    snap = await store.get_facts(["order_40"])
    assert snap.revenue_cop("order_40", frozen_total=None) == 50000

    bus.publish("ads", "changed")  # otros dominios no invalidan
    query.edit("order_40", total_cop=1)
    snap = await store.get_facts(["order_40"])
    assert snap.facts["order_40"].total_cop == 50000


@pytest.mark.asyncio
async def test_expired_value_is_served_and_refreshed_in_background() -> None:
    """Edición hecha directo en Medusa Admin (sin evento): se ve al vencer el
    TTL, sin bloquear la carga de Ads."""
    clock = Clock()
    query = FakeQuery(list(ALL))
    store = OrderFactsStore(query, page_size=2, ttl_s=60, clock=clock)
    await store.get_facts(["order_31"])
    query.edit("order_31", total_cop=150000)

    clock.now += 61
    snap = await store.get_facts(["order_31"])
    assert snap.facts["order_31"].total_cop == 120000  # último valor, sin esperar
    await store.wait_for_refresh()
    snap = await store.get_facts(["order_31"])
    assert snap.facts["order_31"].total_cop == 150000


@pytest.mark.asyncio
async def test_failed_refresh_keeps_last_value_flagged_stale() -> None:
    clock = Clock()
    query = FakeQuery(list(ALL))
    store = OrderFactsStore(query, page_size=2, ttl_s=60, clock=clock)
    await store.get_facts(["order_31"])
    query.available = False
    store.invalidate()

    snap = await store.get_facts(["order_31"])
    assert snap.facts["order_31"].total_cop == 120000
    assert snap.stale is True
    assert snap.revenue_cop("order_31", frozen_total=1) == 120000


@pytest.mark.asyncio
async def test_paging_stops_once_every_order_is_found() -> None:
    query = FakeQuery(list(ALL))
    store = OrderFactsStore(query, page_size=2)
    await store.get_facts(["order_31", "order_40"])
    assert query.list_calls == [0]


@pytest.mark.asyncio
async def test_page_cap_leaves_orders_unresolved() -> None:
    query = FakeQuery(list(ALL))
    store = OrderFactsStore(query, page_size=1, max_pages=1)
    snap = await store.get_facts(["order_32"])
    assert snap.unresolved == frozenset({"order_32"})
    assert snap.revenue_cop("order_32", frozen_total=777) == 777


@pytest.mark.asyncio
async def test_concurrent_readers_share_one_medusa_fetch() -> None:
    query = FakeQuery(list(ALL))
    store = OrderFactsStore(query, page_size=10)
    await asyncio.gather(*(store.get_facts(["order_31"]) for _ in range(5)))
    assert query.list_calls == [0]


# ----------------------------------------------------------------------
# Composición + superficie del SDK
# ----------------------------------------------------------------------


def test_canonical_query_port_feeds_the_shared_facts_store(monkeypatch) -> None:
    """`get_order_query_port()` (lo que usa Orders) graba en el MISMO store
    que devuelve `get_order_facts_port()` (lo que usan Ads y Campañas), y el
    store escucha el bus del dashboard."""
    from src.platform.events import get_dashboard_event_bus
    from src.platform.orders import composition

    monkeypatch.setattr(composition, "_raw_order_query", lambda: FakeQuery([]))
    composition.get_order_query_port.cache_clear()
    composition.get_order_facts_port.cache_clear()
    try:
        store = composition.get_order_facts_port()
        port = composition.get_order_query_port()
        assert isinstance(store, OrderFactsStore)
        assert isinstance(port, RecordingOrderQuery)
        assert port._store is store
        assert store._on_dashboard_event in get_dashboard_event_bus()._listeners
    finally:
        composition.get_order_query_port.cache_clear()
        composition.get_order_facts_port.cache_clear()


def test_sdk_exposes_the_facts_layer() -> None:
    from src.sdk import connectorkit

    assert connectorkit.get_order_facts_port is not None
    assert connectorkit.OrderFactsReadPort is OrderFactsReadPort
    assert connectorkit.OrderFacts is OrderFacts
    assert connectorkit.InMemoryOrderFacts is InMemoryOrderFacts
