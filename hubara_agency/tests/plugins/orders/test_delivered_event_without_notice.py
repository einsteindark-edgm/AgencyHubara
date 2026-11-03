"""Marcar un pedido entregado sin avisar al cliente igual invita a calificar
(decisión del operador, 2026-10-23).

Saltar etapas en el tablero (preparación → entregado) trae «Avisar al cliente»
APAGADO por defecto: el WhatsApp de «entregado» del ETA no sale (bien), pero
la invitación a calificar es otra cosa. Por eso `orders` emite
`OrderDeliveredEvent` con CADA entrega, con o sin aviso, y la invitación
cuelga de él; el aviso del ETA sigue en `OrderStageChangedEvent`.
"""
from __future__ import annotations

import pytest


def _wire(monkeypatch: pytest.MonkeyPatch) -> list:
    import src.platform.orchestration as orchestration
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

    monkeypatch.setattr(orders_api, "_resolve_session_for_order", fake_resolve)
    monkeypatch.setattr(orchestration, "dispatch_envelope_with_client", fake_dispatch)
    monkeypatch.setattr(temporal_client, "get_temporal_client", fake_client)
    monkeypatch.setattr(temporal_client, "get_activity_temporal_client", fake_client)
    monkeypatch.setattr(emit_stage, "_emit_stage_capi", fake_capi)
    return dispatched


async def test_delivered_without_notice_still_announces_the_delivery(monkeypatch) -> None:
    from src.plugins.orders.agent.activities import emit_stage

    dispatched = _wire(monkeypatch)

    result = await emit_stage.emit_order_stage_activity("order_01SKIP", "delivered", None, False)

    assert result == "capi_only"  # sin WhatsApp de «entregado»
    assert [e.event_type for e in dispatched] == ["OrderDeliveredEvent"]
    payload = dispatched[0].payload
    assert (payload["session_id"], payload["order_id"]) == ("wa_10000000001", "order_01SKIP")
    assert payload["occurred_at_ms"] > 0


async def test_delivered_with_notice_announces_both(monkeypatch) -> None:
    from src.plugins.orders.agent.activities import emit_stage

    dispatched = _wire(monkeypatch)

    result = await emit_stage.emit_order_stage_activity("order_01OK", "delivered", None, True)

    assert result == "dispatched"
    assert [e.event_type for e in dispatched] == ["OrderDeliveredEvent", "OrderStageChangedEvent"]


async def test_other_stages_without_notice_announce_nothing(monkeypatch) -> None:
    from src.plugins.orders.agent.activities import emit_stage

    dispatched = _wire(monkeypatch)

    assert await emit_stage.emit_order_stage_activity("order_01SHIP", "shipping", None, False) == "capi_only"
    assert dispatched == []
