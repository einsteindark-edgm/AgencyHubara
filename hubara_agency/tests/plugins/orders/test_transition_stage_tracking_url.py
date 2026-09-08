"""PATCH /api/orders/{id}/stage — `tracking_url` opcional en el body.

Cuando el operador mueve el pedido a "en camino" puede adjuntar el link de
la guía. El link:

  * viaja a la cascada ETA (`_spawn_emit(order_id, stage, tracking_url)`)
    para que el mensaje de WhatsApp que anuncia el cambio lo incluya;
  * queda en la nota del stage history (auditoría: qué guía se mandó);
  * se valida: solo http(s), sin espacios, ≤ 500 chars → si no, 422 y la
    transición NO se aplica (no queremos un pedido "en camino" con un link
    roto que ya no se puede reenviar por template).

Sin `tracking_url` el endpoint es byte-a-byte el de hoy. Fixtures espejadas
de `test_transition_stage_notify_customer.py`.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.orders.command_port import OrderCommandResult

URL = "https://www.coordinadora.com/rastreo?guia=98765432101"


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
        lambda order_id, to_stage, tracking_url=None: spawn_emit_calls.append(
            (order_id, to_stage, tracking_url)
        ),
    )
    monkeypatch.setattr(
        "src.plugins.orders.api._publish_orders_changed", lambda order_id=None: None
    )

    from src.plugins.orders import api as orders_api

    app = FastAPI()
    app.include_router(orders_api.router, prefix="/api/orders")
    return TestClient(app), fake_port, spawn_emit_calls


def test_tracking_url_travels_to_eta_emit_and_stage_note(harness):
    client, fake_port, spawn_emit_calls = harness

    response = client.patch(
        "/api/orders/orders/order_01HX/stage",
        json={"stage": "shipping", "tracking_url": URL},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert spawn_emit_calls == [("order_01HX", "shipping", URL)]
    assert fake_port.commands[0].note == f"Guía de envío: {URL}"


def test_tracking_url_is_trimmed_and_appended_to_existing_note(harness):
    client, fake_port, spawn_emit_calls = harness

    client.patch(
        "/api/orders/orders/order_01HX/stage",
        json={"stage": "shipping", "note": "Salió con Juan", "tracking_url": f"  {URL} "},
    )

    assert spawn_emit_calls == [("order_01HX", "shipping", URL)]
    assert fake_port.commands[0].note == f"Salió con Juan · Guía de envío: {URL}"


def test_without_tracking_url_behaviour_is_unchanged(harness):
    client, fake_port, spawn_emit_calls = harness

    client.patch("/api/orders/orders/order_01HX/stage", json={"stage": "shipping"})
    client.patch(
        "/api/orders/orders/order_01HX/stage",
        json={"stage": "shipping", "tracking_url": ""},
    )

    assert spawn_emit_calls == [
        ("order_01HX", "shipping", None),
        ("order_01HX", "shipping", None),
    ]
    assert [c.note for c in fake_port.commands] == [None, None]


@pytest.mark.parametrize(
    "bad",
    [
        "javascript:alert(1)",
        "ftp://files.example.com/guia",
        "www.servientrega.com/guia/123",  # sin esquema — no linkifica seguro
        "https://x.com/a b",
        "https://" + "a" * 500,
        123,
    ],
)
def test_invalid_tracking_url_is_rejected_before_transition(harness, bad):
    client, fake_port, spawn_emit_calls = harness

    response = client.patch(
        "/api/orders/orders/order_01HX/stage",
        json={"stage": "shipping", "tracking_url": bad},
    )

    assert response.status_code == 422
    assert "tracking_url" in response.json()["detail"]
    assert fake_port.commands == []
    assert spawn_emit_calls == []


# ── Emisión durable: el link viaja en el evento ──────────────────────────


async def test_emit_activity_puts_tracking_url_in_event(monkeypatch):
    """`emit_order_stage_activity(order_id, stage, tracking_url)` emite el
    `OrderStageChangedEvent` con `tracking_url` en el payload — es lo que el
    manifest mapea (`$.tracking_url`) al signal del workflow ETA."""
    import src.platform.orchestration as orchestration
    import src.platform.temporal.client as temporal_client
    from src.plugins.orders import api as orders_api
    from src.plugins.orders.agent.activities.emit_stage import (
        emit_order_stage_activity,
    )

    dispatched: list = []

    async def fake_resolve(**kw):
        return "wa_573001112233"

    async def fake_dispatch(envelope, client):
        dispatched.append(envelope)

    async def fake_client():
        return object()

    monkeypatch.setattr(orders_api, "_resolve_session_for_order", fake_resolve)
    monkeypatch.setattr(orchestration, "dispatch_envelope_with_client", fake_dispatch)
    monkeypatch.setattr(temporal_client, "get_temporal_client", fake_client)

    result = await emit_order_stage_activity("order_01HX", "shipping", URL)

    assert result == "dispatched"
    payload = dispatched[0].payload
    assert payload["to_stage"] == "shipping"
    assert payload["tracking_url"] == URL

    # Sin link (llamada legacy de 2 args): el campo va None, no explota.
    await emit_order_stage_activity("order_01HX", "ready")
    assert dispatched[1].payload["tracking_url"] is None
