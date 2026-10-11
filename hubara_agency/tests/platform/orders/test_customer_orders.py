"""Los pedidos de UNA persona en Medusa — la condición del cupón de bienvenida.

Caso del 2026-10-09: la página ofrece un 5 % «para nuevos clientes en su primer
pedido». Lo que registró la conversación no alcanza: la persona es su número.
En Medusa es el cliente a nombre de la sesión (`wa+<sesión>@hubara.local`, el
que usan el bot y «Crear pedido») y todo cliente con ese teléfono. Sus pedidos
y borradores salen con el MISMO mapeo de la vista Órdenes (etapa, pago, prueba).
"""
from __future__ import annotations

import pytest
import respx
from httpx import Request, Response

from src.platform.medusa.client import HttpMedusaClient
from src.platform.medusa.settings import MedusaSettings
from src.platform.orders.customer_orders import (
    CustomerOrdersUnavailableError,
    InMemoryCustomerOrders,
    MedusaCustomerOrders,
)
from src.platform.orders.facts import OrderFacts
from src.platform.orders.medusa_order_query import MedusaOrderQuery

_BASE_URL = "http://medusa.test"
SESSION = "wa_573001111111"
SESSION_EMAIL = "wa+wa_573001111111@hubara.local"


def _raw(order_id: str, *, display_id: int, metadata: dict | None = None) -> dict:
    return {
        "id": order_id,
        "display_id": display_id,
        "status": "pending",
        "payment_status": "not_paid",
        "fulfillment_status": "not_fulfilled",
        "currency_code": "cop",
        "created_at": "2026-09-20T14:00:00.000Z",
        "updated_at": "2026-09-20T14:00:00.000Z",
        "total": 50000,
        "metadata": metadata or {},
        "items": [],
        "shipping_address": {"first_name": "Ana"},
        "customer": {"first_name": "Ana"},
    }


def _customers(request: Request) -> Response:
    """Por correo: el cliente de la sesión. Por `q` (Medusa busca por pedazo de
    texto): ese, otro con el mismo teléfono escrito distinto y uno que solo
    contiene los dígitos en medio de otro número."""
    params = request.url.params
    if params.get("email") == SESSION_EMAIL:
        rows = [{"id": "cus_wa", "email": SESSION_EMAIL, "phone": "3001111111"}]
    elif params.get("q") == "3001111111":
        rows = [
            {"id": "cus_wa", "email": SESSION_EMAIL, "phone": "3001111111"},
            {"id": "cus_web", "email": "ana@example.com", "phone": "+57 300 111 1111"},
            {"id": "cus_otro", "email": "otro@example.com", "phone": "13001111111999"},
        ]
    else:
        rows = []
    return Response(200, json={"customers": rows, "count": len(rows), "offset": 0, "limit": 20})


@pytest.fixture
async def port():
    settings = MedusaSettings(base_url=_BASE_URL, admin_token="sk")  # type: ignore[call-arg]
    client = HttpMedusaClient(base_url=_BASE_URL, admin_token="sk", timeout=5.0)
    yield MedusaCustomerOrders(client, MedusaOrderQuery(client, settings))
    await client.aclose()


@pytest.mark.asyncio
async def test_the_orders_of_a_person_are_those_of_every_customer_with_their_number(port) -> None:
    with respx.mock(base_url=_BASE_URL) as mock:
        mock.get("/admin/customers").mock(side_effect=_customers)
        orders = mock.get("/admin/orders").mock(
            return_value=Response(200, json={"orders": [_raw("order_web", display_id=12)], "count": 1})
        )
        drafts = mock.get("/admin/draft-orders").mock(
            return_value=Response(
                200,
                json={
                    "draft_orders": [_raw("order_wa", display_id=40, metadata={"hubara_stage": "cancelled"})],
                    "count": 1,
                },
            )
        )
        facts = await port.orders_of(SESSION)

    assert sorted(f.order_id for f in facts) == ["order_wa", "order_web"]
    by_id = {f.order_id: f for f in facts}
    assert by_id["order_wa"].stage == "cancelled" and by_id["order_wa"].is_draft
    assert by_id["order_web"].stage == "new" and not by_id["order_web"].is_draft
    for call in (orders.calls.last, drafts.calls.last):
        assert sorted(call.request.url.params.get_list("customer_id[]")) == ["cus_wa", "cus_web"]


@pytest.mark.asyncio
async def test_a_number_without_customers_has_no_orders_and_no_order_listing(port) -> None:
    with respx.mock(base_url=_BASE_URL, assert_all_called=False) as mock:
        mock.get("/admin/customers").mock(
            return_value=Response(200, json={"customers": [], "count": 0, "offset": 0, "limit": 20})
        )
        orders = mock.get("/admin/orders")
        drafts = mock.get("/admin/draft-orders")
        assert await port.orders_of(SESSION) == ()

    assert not orders.called and not drafts.called


@pytest.mark.asyncio
async def test_when_medusa_does_not_answer_it_says_so_instead_of_returning_nothing(port) -> None:
    with respx.mock(base_url=_BASE_URL) as mock:
        mock.get("/admin/customers").mock(return_value=Response(503, text="down"))
        with pytest.raises(CustomerOrdersUnavailableError):
            await port.orders_of(SESSION)


@pytest.mark.asyncio
async def test_the_official_fake_serves_orders_by_session_or_fails_like_medusa() -> None:
    fact = OrderFacts(
        order_id="order_1", display_id="#1", total_cop=45000, currency_code="cop",
        pay_status="paid", stage="delivered", customer="Ana", is_draft=False,
    )
    fake = InMemoryCustomerOrders({SESSION: [fact]})

    assert await fake.orders_of(SESSION) == (fact,)
    assert await fake.orders_of("wa_573000000000") == ()
    with pytest.raises(CustomerOrdersUnavailableError):
        await InMemoryCustomerOrders(available=False).orders_of(SESSION)


def test_without_medusa_configured_nobody_has_orders_there(monkeypatch) -> None:
    from src.platform.orders import composition
    from src.platform.orders.customer_orders import NoCustomerOrders
    from src.platform.orders.empty_query import EmptyOrderQuery

    monkeypatch.setattr(composition, "_raw_order_query", lambda: EmptyOrderQuery())
    composition.get_customer_orders_port.cache_clear()
    try:
        assert isinstance(composition.get_customer_orders_port(), NoCustomerOrders)
    finally:
        composition.get_customer_orders_port.cache_clear()


async def test_with_medusa_the_port_reads_the_customers_of_the_number(monkeypatch) -> None:
    from src.platform.orders import composition

    settings = MedusaSettings(base_url=_BASE_URL, admin_token="sk")  # type: ignore[call-arg]
    client = HttpMedusaClient(base_url=_BASE_URL, admin_token="sk", timeout=5.0)
    monkeypatch.setattr(composition, "_raw_order_query", lambda: MedusaOrderQuery(client, settings))
    monkeypatch.setattr(composition, "get_medusa_client", lambda: client)
    composition.get_customer_orders_port.cache_clear()
    try:
        port = composition.get_customer_orders_port()
        with respx.mock(base_url=_BASE_URL, assert_all_called=False) as mock:
            customers = mock.get("/admin/customers").mock(
                return_value=Response(200, json={"customers": [], "count": 0, "offset": 0, "limit": 20})
            )
            assert await port.orders_of(SESSION) == ()
        assert customers.called
    finally:
        composition.get_customer_orders_port.cache_clear()
        await client.aclose()
