"""PATCH /api/orders/{id}/stage — flag opcional `notify_customer` en el body.

Comportamiento a fijar (para el agente order-sentinel, que infiere
transiciones de la conversación humana — el humano YA le avisó al cliente
por chat, la notificación ETA duplicaría el mensaje):

  * `notify_customer: false` → tras una transición exitosa la emisión
    arranca IGUAL (`_spawn_emit` → EmitOrderStageWorkflow) pero marcada
    `notify_customer=False`: sale el evento CAPI de la etapa (Meta sí debe
    saber que se entregó) y NO el WhatsApp al cliente.
    `_publish_orders_changed` (SSE dashboard) se llama igual.
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
    spawn_emit_calls: list[tuple[str, str, bool]] = []
    publish_calls: list[str | None] = []

    monkeypatch.setattr(
        "src.plugins.orders.api.get_order_command_port",
        lambda: fake_port,
    )
    monkeypatch.setattr(
        "src.plugins.orders.api._spawn_emit",
        lambda order_id, to_stage, tracking_url=None, notify_customer=True: (
            spawn_emit_calls.append((order_id, to_stage, notify_customer))
        ),
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


def test_transition_stage_notify_customer_false_emits_silently_and_publishes_sse(
    harness,
):
    """notify_customer=false + transición exitosa → la emisión arranca en
    modo silencioso (CAPI sí, WhatsApp no), el dashboard se entera por SSE y
    el response shape es el de siempre."""
    client, _, spawn_emit_calls, publish_calls = harness

    response = client.patch(
        "/api/orders/orders/order_01HX/stage",
        json={"stage": "ready", "notify_customer": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["current_stage"] == "ready"
    assert spawn_emit_calls == [("order_01HX", "ready", False)], (
        "notify_customer=false debe emitir la etapa SIN avisar al cliente "
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
    assert spawn_emit_calls == [("order_01HX", "ready", True)], (
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


def test_skip_to_delivered_without_notice_is_forced_and_silent(harness):
    """Operador olvidó mover el pedido y ya se entregó: salto directo
    preparing → delivered con `force` + `notify_customer=false`. El command
    lleva force y la nota del salto (traza honesta en el stage history), y la
    emisión sale silenciosa."""
    client, fake_port, spawn_emit_calls, _ = harness
    fake_port.result = OrderCommandResult(
        success=True, order_id="order_01HX", current_stage="delivered"
    )

    response = client.patch(
        "/api/orders/orders/order_01HX/stage",
        json={
            "stage": "delivered",
            "force": True,
            "notify_customer": False,
            "note": "Salto manual: se omitió Lista, En camino",
        },
    )

    assert response.json()["success"] is True
    (cmd,) = fake_port.commands
    assert (cmd.to_stage, cmd.force, cmd.note) == (
        "delivered", True, "Salto manual: se omitió Lista, En camino",
    )
    assert spawn_emit_calls == [("order_01HX", "delivered", False)]


# ── Emisión durable: modo silencioso ─────────────────────────────────────


async def test_emit_activity_silent_sends_capi_but_not_whatsapp(monkeypatch):
    """`emit_order_stage_activity(..., notify_customer=False)` encola el evento
    CAPI de la etapa (OrderDelivered) pero NO despacha el
    OrderStageChangedEvent que dispara el WhatsApp del Agente ETA."""
    import src.platform.orchestration as orchestration
    import src.platform.temporal.client as temporal_client
    from src.plugins.orders import api as orders_api
    from src.plugins.orders.agent.activities import emit_stage

    dispatched: list = []
    capi: list = []

    async def fake_resolve(**kw):
        return "wa_573001234567"

    async def fake_dispatch(envelope, client):
        dispatched.append(envelope)

    async def fake_client():
        return object()

    async def fake_capi(session_id, order_id, to_stage):
        capi.append((session_id, order_id, to_stage))

    monkeypatch.setattr(orders_api, "_resolve_session_for_order", fake_resolve)
    monkeypatch.setattr(orchestration, "dispatch_envelope_with_client", fake_dispatch)
    monkeypatch.setattr(temporal_client, "get_temporal_client", fake_client)
    monkeypatch.setattr(emit_stage, "_emit_stage_capi", fake_capi)

    result = await emit_stage.emit_order_stage_activity(
        "order_01HX", "delivered", None, False
    )

    assert result == "capi_only"
    assert capi == [("wa_573001234567", "order_01HX", "delivered")]
    assert dispatched == []

    # Default (llamada legacy de 3 args): avisa como siempre.
    assert await emit_stage.emit_order_stage_activity("order_01HX", "delivered") == "dispatched"
    assert len(dispatched) == 1


def test_emit_workflow_input_carries_notify_customer():
    """El input del EmitOrderStageWorkflow lleva `notify_customer`; runs
    viejos sin la key siguen avisando (default True)."""
    from src.plugins.orders.agent.workflows.emit_stage import activity_args

    assert activity_args(
        {"order_id": "o1", "to_stage": "delivered", "notify_customer": False}
    ) == ["o1", "delivered", None, False]
    assert activity_args({"order_id": "o1", "to_stage": "ready"}) == [
        "o1", "ready", None, True,
    ]
