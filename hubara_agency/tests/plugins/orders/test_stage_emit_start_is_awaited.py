"""El aviso de etapa se ARRANCA antes de responder, y si Temporal no lo acepta
queda en el buzón hasta que sale (auditoría de Temporal, Ola 0; premortem
2026-10-16).

`/schedule`, `/stage` y `/cancel` aplican la transición en Medusa y arrancan
`EmitOrderStageWorkflow` (el aviso al cliente por el Agente ETA + el evento
CAPI). Antes el start iba en un `asyncio.create_task` que solo logueaba si
fallaba: el operador veía 200 y el aviso se perdía. La Ola 0 lo esperó y
respondía 503, pero el premortem vio que eso también lo perdía: el tablero
devolvía la tarjeta a la columna vieja, el detalle no se veía («API error
503»), ninguna pantalla deja reintentar una etapa que ya cambió, «Confirmar
pago» se trababa en su primer paso y el Order Sentinel contaba como fallida
una transición que sí se aplicó. Una falla técnica no pasa al equipo
(auto-recuperación + alerta): el aviso queda en el buzón del vault, la API
responde 200 con `notice_pending` y lo reintenta sola hasta que sale.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from src.platform.orders.command_port import OrderCommandResult

ORDER_ID = "order_01HX"

# (método, ruta, body, etapa que deja el comando)
ENDPOINTS = [
    pytest.param("patch", f"/api/orders/orders/{ORDER_ID}/stage", {"stage": "ready"}, "ready", id="stage"),
    pytest.param(
        "patch", f"/api/orders/orders/{ORDER_ID}/schedule", {"delivery_iso": "2026-10-20"}, "preparing",
        id="schedule",
    ),
    pytest.param("post", f"/api/orders/orders/{ORDER_ID}/cancel", {}, "cancelled", id="cancel"),
]


class _Port:
    """Comando exitoso: la etapa quedó guardada en Medusa."""

    def __init__(self, stage: str) -> None:
        self.stage = stage

    async def _ok(self, command) -> OrderCommandResult:
        return OrderCommandResult(success=True, order_id=ORDER_ID, current_stage=self.stage)

    transition_stage = schedule_delivery = cancel_order = _ok


class _TemporalClient:
    def __init__(self, start_raises: Exception | None = None) -> None:
        self.start_raises = start_raises
        self.starts: list[dict] = []

    async def start_workflow(self, workflow, arg, **kwargs):
        self.starts.append({"workflow": workflow, "arg": arg, **kwargs})
        if self.start_raises is not None:
            raise self.start_raises


@pytest.fixture
def make_api(monkeypatch):
    def _make(stage: str, temporal: _TemporalClient | None = None, client_factory=None):
        published: list[str | None] = []

        async def _client():
            return temporal

        monkeypatch.setattr("src.plugins.orders.api.get_order_command_port", lambda: _Port(stage))
        monkeypatch.setattr("src.platform.temporal.client.get_temporal_client", client_factory or _client)
        monkeypatch.setattr(
            "src.plugins.orders.api._publish_orders_changed", lambda order_id=None: published.append(order_id)
        )
        from src.plugins.orders import api as orders_api

        app = FastAPI()
        app.include_router(orders_api.router, prefix="/api/orders")
        return TestClient(app), published

    return _make


@pytest.mark.parametrize(("method", "path", "body", "stage"), ENDPOINTS)
def test_a_notice_temporal_did_not_take_is_parked_and_the_request_succeeds(make_api, method, path, body, stage):
    from src.plugins.orders import stage_notice_outbox as outbox

    temporal = _TemporalClient(start_raises=RPCError("temporal caído", RPCStatusCode.UNAVAILABLE, b""))
    http, published = make_api(stage, temporal)

    response = getattr(http, method)(path, json=body)

    assert response.status_code == 200, f"la etapa sí cambió: no es un error del operador ({response.status_code})"
    assert response.json()["success"] is True
    assert response.json()["notice_pending"] is True
    assert published == [ORDER_ID], "la etapa sí cambió: el dashboard se refresca igual"
    [parked] = outbox.pending()
    assert parked.workflow_id == f"order-stage-changed-{ORDER_ID}-{stage}"
    assert parked.workflow_input["to_stage"] == stage
    assert "temporal caído" in parked.last_error


@pytest.mark.parametrize(("method", "path", "body", "stage"), ENDPOINTS)
def test_started_emit_answers_200_after_the_start(make_api, method, path, body, stage):
    from src.plugins.orders import stage_notice_outbox as outbox

    temporal = _TemporalClient()
    http, published = make_api(stage, temporal)

    response = getattr(http, method)(path, json=body)

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert "notice_pending" not in response.json()
    [start] = temporal.starts
    assert start["workflow"] == "EmitOrderStageWorkflow"
    assert start["id"] == f"order-stage-changed-{ORDER_ID}-{stage}"
    assert 0 < start["rpc_timeout"].total_seconds() <= 10, "un Temporal que no contesta colgaba el request"
    assert published == [ORDER_ID]
    assert outbox.pending() == []


def test_emit_already_in_flight_is_not_an_error(make_api):
    """Mismo (pedido, etapa) ya en vuelo: Temporal lo dedupea por id — 200."""
    from src.plugins.orders import stage_notice_outbox as outbox

    temporal = _TemporalClient(start_raises=WorkflowAlreadyStartedError("order-stage-changed", "Emit"))
    http, _ = make_api("ready", temporal)

    response = http.patch(f"/api/orders/orders/{ORDER_ID}/stage", json={"stage": "ready"})

    assert response.status_code == 200
    assert "notice_pending" not in response.json()
    assert outbox.pending() == []


def test_a_temporal_that_does_not_answer_does_not_hold_the_request(make_api, monkeypatch):
    """Conectar a un Temporal inalcanzable podía tardar lo que tarde el TCP del
    sistema: la app cortaba a los 30 s con «sin conexión» y el Order Sentinel a
    los 15. El arranque tiene tope; pasado, el aviso va al buzón."""
    from src.plugins.orders import api as orders_api
    from src.plugins.orders import stage_notice_outbox as outbox

    async def hangs():
        await asyncio.sleep(3600)

    monkeypatch.setattr(orders_api, "_START_TIMEOUT_S", 0.2)
    http, _ = make_api("ready", client_factory=hangs)

    started = time.monotonic()
    response = http.patch(f"/api/orders/orders/{ORDER_ID}/stage", json={"stage": "ready"})

    assert time.monotonic() - started < 5
    assert response.status_code == 200 and response.json()["notice_pending"] is True
    assert len(outbox.pending()) == 1


def test_the_api_sends_parked_notices_on_its_own(make_api, monkeypatch):
    """Al arrancar, la API vacía el buzón y lo sigue mirando: cuando Temporal
    vuelve, el aviso sale sin que nadie haga nada."""
    from src.plugins.orders import api as orders_api
    from src.plugins.orders import stage_notice_outbox as outbox

    outbox.park("order-stage-changed-order_01HX-shipping", {"order_id": ORDER_ID, "to_stage": "shipping"}, "caído")
    temporal = _TemporalClient()
    monkeypatch.setattr(orders_api, "_DRAIN_EVERY_S", 0.05)
    monkeypatch.setattr(orders_api, "_drain_task", None)
    http, _ = make_api("shipping", temporal)

    with http:
        deadline = time.monotonic() + 5
        while outbox.pending() and time.monotonic() < deadline:
            time.sleep(0.05)

    assert outbox.pending() == []
    assert [s["id"] for s in temporal.starts] == ["order-stage-changed-order_01HX-shipping"]


class TestTheOutbox:
    @pytest.mark.asyncio
    async def test_each_parked_notice_starts_once_and_is_forgotten(self) -> None:
        from src.plugins.orders import stage_notice_outbox as outbox

        outbox.park("order-stage-changed-a-ready", {"order_id": "a", "to_stage": "ready"}, "caído")
        outbox.park("order-stage-changed-b-shipping", {"order_id": "b", "to_stage": "shipping"}, "caído")
        started: list[tuple[str, dict]] = []

        async def start(workflow_id: str, workflow_input: dict) -> None:
            started.append((workflow_id, workflow_input))

        summary = await outbox.drain(start)

        assert sorted(w for w, _ in started) == ["order-stage-changed-a-ready", "order-stage-changed-b-shipping"]
        assert outbox.pending() == []
        assert summary == {"sent": 2, "pending": 0, "given_up": 0}

    @pytest.mark.asyncio
    async def test_a_notice_that_still_fails_stays_with_its_attempts(self) -> None:
        from src.plugins.orders import stage_notice_outbox as outbox

        outbox.park("order-stage-changed-a-ready", {"order_id": "a", "to_stage": "ready"}, "caído")

        async def start(workflow_id: str, workflow_input: dict) -> None:
            raise RPCError("sigue caído", RPCStatusCode.UNAVAILABLE, b"")

        summary = await outbox.drain(start)

        [notice] = outbox.pending()
        assert (notice.attempts, notice.last_error) == (1, "sigue caído")
        assert summary == {"sent": 0, "pending": 1, "given_up": 0}

    @pytest.mark.asyncio
    async def test_a_notice_already_in_flight_counts_as_sent(self) -> None:
        """El arranque original pudo haber llegado (la respuesta se perdió): un
        `WorkflowAlreadyStartedError` es el aviso que ya salió."""
        from src.plugins.orders import stage_notice_outbox as outbox

        outbox.park("order-stage-changed-a-ready", {"order_id": "a", "to_stage": "ready"}, "timeout")

        async def start(workflow_id: str, workflow_input: dict) -> None:
            raise WorkflowAlreadyStartedError(workflow_id, "EmitOrderStageWorkflow")

        assert (await outbox.drain(start))["sent"] == 1
        assert outbox.pending() == []

    @pytest.mark.asyncio
    async def test_a_notice_stuck_for_a_day_is_given_up_out_loud(self, caplog) -> None:
        from src.plugins.orders import stage_notice_outbox as outbox

        a_day_ago = int(time.time() * 1000) - int(outbox.MAX_AGE.total_seconds() * 1000) - 1
        outbox.park("order-stage-changed-a-ready", {"order_id": "a", "to_stage": "ready"}, "caído", now_ms=a_day_ago)

        async def start(workflow_id: str, workflow_input: dict) -> None:
            raise AssertionError("no se reintenta un aviso de hace un día")

        with caplog.at_level("ERROR"):
            summary = await outbox.drain(start)

        assert summary == {"sent": 0, "pending": 0, "given_up": 1}
        assert outbox.pending() == []
        assert any("order-stage-changed-a-ready" in r.getMessage() for r in caplog.records)
