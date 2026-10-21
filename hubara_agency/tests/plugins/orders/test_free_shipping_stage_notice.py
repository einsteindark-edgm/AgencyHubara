"""El aviso «en camino» de un pedido con envío gratis (operador, 2026-10-20).

Al despachar, el operador escribe el costo real del envío (la app lo exige) y
viaja en el evento hasta el WhatsApp del cliente: «Valor del envío $X / Total»
y, con contra entrega, «pagas {Total} al repartidor». Con envío gratis ese
costo lo paga la tienda: el evento sale sin él, así el cliente no lo ve ni se
le suma. Si no se puede leer el pedido, sale como siempre (con su aviso).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _wire(monkeypatch: pytest.MonkeyPatch, order) -> list:
    import src.platform.orchestration as orchestration
    import src.platform.orders.composition as composition
    import src.platform.temporal.client as temporal_client
    from src.plugins.orders import api as orders_api
    from src.plugins.orders.agent.activities import emit_stage

    dispatched: list = []

    async def fake_resolve(**kw):
        return "wa_10000000001"

    async def fake_dispatch(envelope, client):
        dispatched.append(envelope)

    async def fake_client():
        return object()

    async def fake_capi(session_id, order_id, to_stage):
        return None

    class _Query:
        async def get(self, order_id: str):
            if isinstance(order, Exception):
                raise order
            return order

    monkeypatch.setattr(orders_api, "_resolve_session_for_order", fake_resolve)
    monkeypatch.setattr(orchestration, "dispatch_envelope_with_client", fake_dispatch)
    monkeypatch.setattr(temporal_client, "get_temporal_client", fake_client)
    monkeypatch.setattr(emit_stage, "_emit_stage_capi", fake_capi)
    monkeypatch.setattr(composition, "get_order_query_port", lambda: _Query())
    return dispatched


def _detail(free: bool) -> SimpleNamespace:
    return SimpleNamespace(summary=SimpleNamespace(free_shipping=free))


async def test_a_free_shipping_order_is_announced_without_the_store_shipping_cost(monkeypatch) -> None:
    from src.plugins.orders.agent.activities import emit_stage

    dispatched = _wire(monkeypatch, _detail(free=True))

    result = await emit_stage.emit_order_stage_activity("order_01FREE", "shipping", None, True, 18000)

    assert result == "dispatched"
    assert dispatched[0].payload["shipping_cost"] is None


async def test_an_order_with_shipping_keeps_announcing_its_cost(monkeypatch) -> None:
    from src.plugins.orders.agent.activities import emit_stage

    dispatched = _wire(monkeypatch, _detail(free=False))

    await emit_stage.emit_order_stage_activity("order_01SHIP", "shipping", None, True, 12000)

    assert dispatched[0].payload["shipping_cost"] == 12000


async def test_if_the_order_cannot_be_read_the_notice_goes_as_always(monkeypatch) -> None:
    from src.plugins.orders.agent.activities import emit_stage

    dispatched = _wire(monkeypatch, RuntimeError("medusa caída"))

    result = await emit_stage.emit_order_stage_activity("order_01SHIP", "shipping", None, True, 12000)

    assert result == "dispatched" and dispatched[0].payload["shipping_cost"] == 12000
