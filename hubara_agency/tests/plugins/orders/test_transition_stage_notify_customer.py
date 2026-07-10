"""PATCH /api/orders/{id}/stage — flag opcional `notify_customer` en el body.

Comportamiento a fijar (para el agente order-sentinel, que infiere
transiciones de la conversación humana — el humano YA le avisó al cliente
por chat, la notificación ETA duplicaría el mensaje):

  * `notify_customer: false` → tras una transición exitosa NO se dispara la
    cascada ETA (`_spawn_emit` → EmitOrderStageWorkflow), pero
    `_publish_orders_changed` (SSE dashboard) SÍ se llama igual.
  * `notify_customer: true` u omitido → comportamiento actual intacto
    (se llama `_spawn_emit`).
  * El flag NO afecta validación ni response shape.

Patrón de fixtures espejado de `test_orders_api.py`: monkeypatch del
composition factory en el IMPORT SITE (`src.plugins.orders.api.*`) + app
FastAPI fresco + TestClient. `_spawn_emit` y `_publish_orders_changed` se
reemplazan por spies (evita tocar Temporal / event bus reales).

NOTA (verificado leyendo src/plugins/orders/api/__init__.py): el endpoint
`confirm-payment` NO llama `_spawn_emit` hoy — no hay caso análogo acá.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.orders.command_port import OrderCommandResult


class FakeOrderCommandPort:
    """Fake write-side port: devuelve el resultado configurado y graba
    los commands recibidos (para asertar que el flag no contamina el cmd)."""

    def __init__(self, result: OrderCommandResult) -> None:
        self.result = result
        self.commands: list = []

    async def transition_stage(self, command) -> OrderCommandResult:
        self.commands.append(command)
        return self.result


@pytest.fixture
def harness(monkeypatch):
    """App + fake port + spies sobre _spawn_emit / _publish_orders_changed."""
    fake_port = FakeOrderCommandPort(
        OrderCommandResult(
            success=True,
            order_id="order_01HX",
            current_stage="ready",
        )
    )
    spawn_emit_calls: list[tuple[str, str]] = []
    publish_calls: list[str | None] = []

    monkeypatch.setattr(
        "src.plugins.orders.api.get_order_command_port",
        lambda: fake_port,
    )
    monkeypatch.setattr(
        "src.plugins.orders.api._spawn_emit",
        lambda order_id, to_stage: spawn_emit_calls.append((order_id, to_stage)),
    )
    monkeypatch.setattr(
        "src.plugins.orders.api._publish_orders_changed",
        lambda order_id=None: publish_calls.append(order_id),
    )

    from src.plugins.orders import api as orders_api

    app = FastAPI()
    app.include_router(orders_api.router, prefix="/api/orders")
    client = TestClient(app)
    return client, fake_port, spawn_emit_calls, publish_calls


def test_transition_stage_notify_customer_false_skips_eta_emit_but_publishes_sse(
    harness,
):
    """notify_customer=false + transición exitosa → la cascada ETA NO
    arranca (el humano ya avisó por chat), pero el dashboard SÍ se entera
    por SSE y el response shape es el de siempre."""
    client, _, spawn_emit_calls, publish_calls = harness

    response = client.patch(
        "/api/orders/orders/order_01HX/stage",
        json={"stage": "ready", "notify_customer": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["current_stage"] == "ready"
    assert spawn_emit_calls == [], (
        "notify_customer=false NO debe disparar la cascada ETA "
        f"(_spawn_emit fue llamado con {spawn_emit_calls!r})"
    )
    assert publish_calls == ["order_01HX"], (
        "el SSE del dashboard (_publish_orders_changed) debe dispararse "
        "igual aunque notify_customer sea false"
    )


def test_transition_stage_without_notify_customer_still_emits(harness):
    """Regresión del default: body sin el campo → comportamiento actual
    intacto (la cascada ETA arranca)."""
    client, _, spawn_emit_calls, publish_calls = harness

    response = client.patch(
        "/api/orders/orders/order_01HX/stage",
        json={"stage": "ready"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert spawn_emit_calls == [("order_01HX", "ready")], (
        "sin notify_customer en el body, _spawn_emit debe llamarse como hoy"
    )
    assert publish_calls == ["order_01HX"]


def test_transition_stage_propaga_by_al_command(harness):
    """El `by` del body viaja al TransitionStageCommand (atribución en el
    stage history: el sentinel manda by="order-sentinel"); omitido → "human"."""
    client, fake_port, _, _ = harness

    client.patch(
        "/api/orders/orders/order_01HX/stage",
        json={"stage": "ready", "by": "order-sentinel", "notify_customer": False},
    )
    client.patch(
        "/api/orders/orders/order_01HX/stage",
        json={"stage": "ready"},
    )

    assert [c.by for c in fake_port.commands] == ["order-sentinel", "human"]


def test_transition_stage_notify_customer_false_failed_transition_emits_nothing(
    harness,
):
    """notify_customer=false + transición fallida (success=False) → nada
    emite, igual que hoy: ni ETA ni SSE."""
    client, fake_port, spawn_emit_calls, publish_calls = harness
    fake_port.result = OrderCommandResult(
        success=False,
        order_id="order_01HX",
        current_stage=None,
        error_detail="invalid_transition: delivered -> ready",
    )

    response = client.patch(
        "/api/orders/orders/order_01HX/stage",
        json={"stage": "ready", "notify_customer": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["error_detail"].startswith("invalid_transition:")
    assert spawn_emit_calls == []
    assert publish_calls == []
