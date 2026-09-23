"""PATCH /api/orders/{id}/stage — `shipping_cost` opcional (valor del envío).

Al marcar "en camino" el operador puede escribir el valor del envío. Ese
valor (COP entero, sin decimales):

  * viaja a la cascada ETA (`_spawn_emit(..., shipping_cost=)`) → input del
    `EmitOrderStageWorkflow` → `OrderStageChangedEvent.shipping_cost` →
    manifest (`$.shipping_cost`) → signal del ETA → mensaje de WhatsApp;
  * queda en la nota del stage history (auditoría);
  * se valida: entero ≥ 0 y ≤ 10.000.000 → si no, 422 y la transición NO
    se aplica.

Sin `shipping_cost` el endpoint es byte-a-byte el de hoy.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.orders.command_port import OrderCommandResult


class FakeOrderCommandPort:
    def __init__(self, result: OrderCommandResult) -> None:
        self.result = result
        self.commands: list = []

    async def transition_stage(self, command) -> OrderCommandResult:
        self.commands.append(command)
        return self.result


@pytest.fixture
def harness(monkeypatch):
    fake_port = FakeOrderCommandPort(
        OrderCommandResult(success=True, order_id="order_01HX", current_stage="shipping")
    )
    spawn_emit_calls: list[tuple] = []

    monkeypatch.setattr(
        "src.plugins.orders.api.get_order_command_port", lambda: fake_port
    )
    monkeypatch.setattr(
        "src.plugins.orders.api._spawn_emit",
        lambda order_id, to_stage, tracking_url=None, notify_customer=True, shipping_cost=None: (
            spawn_emit_calls.append((order_id, to_stage, tracking_url, shipping_cost))
        ),
    )
    monkeypatch.setattr(
        "src.plugins.orders.api._publish_orders_changed", lambda order_id=None: None
    )

    from src.plugins.orders import api as orders_api

    app = FastAPI()
    app.include_router(orders_api.router, prefix="/api/orders")
    return TestClient(app), fake_port, spawn_emit_calls


def test_shipping_cost_travels_to_eta_emit_and_stage_note(harness):
    client, fake_port, spawn_emit_calls = harness

    response = client.patch(
        "/api/orders/orders/order_01HX/stage",
        json={"stage": "shipping", "shipping_cost": 12000},
    )

    assert response.status_code == 200
    assert spawn_emit_calls == [("order_01HX", "shipping", None, 12000)]
    assert fake_port.commands[0].note == "Valor del envío: $ 12.000"


def test_shipping_cost_is_persisted_as_the_real_shipping(harness):
    """El valor del modal es el envío REAL: el comando lo persiste y desde ahí
    reemplaza al estimado en todo Hubara (total, OrderFacts, cobro)."""
    client, fake_port, _ = harness

    client.patch(
        "/api/orders/orders/order_01HX/stage",
        json={"stage": "shipping", "shipping_cost": 12000},
    )
    client.patch("/api/orders/orders/order_01HX/stage", json={"stage": "shipping"})

    assert [c.shipping_cost_cop for c in fake_port.commands] == [12000, None]


def test_shipping_cost_and_tracking_url_share_the_note(harness):
    client, fake_port, spawn_emit_calls = harness
    url = "https://www.coordinadora.com/rastreo?guia=98765432101"

    client.patch(
        "/api/orders/orders/order_01HX/stage",
        json={"stage": "shipping", "note": "Salió con Juan", "tracking_url": url, "shipping_cost": 8500},
    )

    assert spawn_emit_calls == [("order_01HX", "shipping", url, 8500)]
    assert fake_port.commands[0].note == (
        f"Salió con Juan · Guía de envío: {url} · Valor del envío: $ 8.500"
    )


def test_without_shipping_cost_behaviour_is_unchanged(harness):
    client, fake_port, spawn_emit_calls = harness

    client.patch("/api/orders/orders/order_01HX/stage", json={"stage": "shipping"})
    client.patch(
        "/api/orders/orders/order_01HX/stage",
        json={"stage": "shipping", "shipping_cost": None},
    )

    assert spawn_emit_calls == [
        ("order_01HX", "shipping", None, None),
        ("order_01HX", "shipping", None, None),
    ]
    assert [c.note for c in fake_port.commands] == [None, None]


@pytest.mark.parametrize("bad", ["12.000", -1, 1.5, True, 10_000_001, {"v": 1}])
def test_invalid_shipping_cost_is_422_and_does_not_transition(harness, bad):
    client, fake_port, spawn_emit_calls = harness

    response = client.patch(
        "/api/orders/orders/order_01HX/stage",
        json={"stage": "shipping", "shipping_cost": bad},
    )

    assert response.status_code == 422, bad
    assert "shipping_cost" in response.json()["detail"]
    assert fake_port.commands == []
    assert spawn_emit_calls == []


# ── Emisión durable: el valor llega al evento ────────────────────────────


def test_emit_workflow_input_carries_shipping_cost():
    from src.plugins.orders.agent.workflows.emit_stage import activity_args

    assert activity_args(
        {"order_id": "o1", "to_stage": "shipping", "shipping_cost": 12000}
    ) == ["o1", "shipping", None, True, 12000]
    # Runs viejos sin la key → sin valor.
    assert activity_args({"order_id": "o1", "to_stage": "shipping"}) == [
        "o1", "shipping", None, True, None,
    ]


async def test_emit_activity_puts_shipping_cost_in_the_event(monkeypatch):
    import src.platform.orchestration as orchestration
    import src.platform.temporal.client as temporal_client
    from src.plugins.orders import api as orders_api
    from src.plugins.orders.agent.activities import emit_stage

    dispatched: list = []

    async def fake_resolve(**kw):
        return "wa_573001234567"

    async def fake_dispatch(envelope, client):
        dispatched.append(envelope)

    async def fake_client():
        return object()

    async def fake_capi(session_id, order_id, to_stage):
        return None

    monkeypatch.setattr(orders_api, "_resolve_session_for_order", fake_resolve)
    monkeypatch.setattr(orchestration, "dispatch_envelope_with_client", fake_dispatch)
    monkeypatch.setattr(temporal_client, "get_temporal_client", fake_client)
    monkeypatch.setattr(emit_stage, "_emit_stage_capi", fake_capi)

    result = await emit_stage.emit_order_stage_activity(
        "order_01HX", "shipping", None, True, 12000
    )

    assert result == "dispatched"
    assert dispatched[0].payload["shipping_cost"] == 12000
    assert dispatched[0].payload["tracking_url"] is None

    # Llamada legacy (sin el 5º arg) → sin valor.
    await emit_stage.emit_order_stage_activity("order_01HX", "shipping")
    assert dispatched[1].payload["shipping_cost"] is None


def test_manifest_maps_shipping_cost_into_the_eta_signal():
    from src.platform.orchestration import envelope_for
    from src.platform.plugin_manifest import get_transitions
    from src.plugins.orders.shared.contracts.events import OrderStageChangedEvent

    env = envelope_for(
        OrderStageChangedEvent(
            session_id="wa_573000000000",
            order_id="order_01TEST",
            to_stage="shipping",
            shipping_cost=12000,
        ),
        source_plugin="orders",
        source_worker="reconcile",
    )
    matching = [t for t in get_transitions("orders", "reconcile") if t.matches(env)]
    assert len(matching) == 1
    assert matching[0].action.input_mapping["shipping_cost"] == "$.shipping_cost"
    assert matching[0].action.input_mapping["tracking_url"] == "$.tracking_url"
