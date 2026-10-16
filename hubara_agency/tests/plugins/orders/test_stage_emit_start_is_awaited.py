"""El aviso de etapa se ARRANCA antes de responder (auditoría Temporal, Ola 0).

`/schedule`, `/stage` y `/cancel` aplican la transición en Medusa y arrancan
`EmitOrderStageWorkflow` (el aviso al cliente por el Agente ETA + el evento
CAPI). Antes el start iba en un `asyncio.create_task` que solo logueaba si
fallaba: el operador veía 200 y el aviso se perdía sin que nadie se enterara.
Ahora se espera el start: si Temporal no lo acepta, 503 (como "Enviar ahora" de
la foto) — la etapa YA quedó guardada, así que el dashboard se refresca igual
y reintentar la misma etapa es un no-op en Medusa que vuelve a arrancar el aviso.
"""
from __future__ import annotations

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
    def _make(stage: str, temporal: _TemporalClient):
        published: list[str | None] = []

        async def _client():
            return temporal

        monkeypatch.setattr("src.plugins.orders.api.get_order_command_port", lambda: _Port(stage))
        monkeypatch.setattr("src.platform.temporal.client.get_temporal_client", _client)
        monkeypatch.setattr(
            "src.plugins.orders.api._publish_orders_changed", lambda order_id=None: published.append(order_id)
        )
        from src.plugins.orders import api as orders_api

        app = FastAPI()
        app.include_router(orders_api.router, prefix="/api/orders")
        return TestClient(app), published

    return _make


@pytest.mark.parametrize(("method", "path", "body", "stage"), ENDPOINTS)
def test_start_failure_answers_503_and_still_refreshes_the_dashboard(make_api, method, path, body, stage):
    temporal = _TemporalClient(start_raises=RPCError("temporal caído", RPCStatusCode.UNAVAILABLE, b""))
    http, published = make_api(stage, temporal)

    response = getattr(http, method)(path, json=body)

    assert response.status_code == 503, (
        f"si el aviso de etapa no arranca el operador debe enterarse; respondió {response.status_code}"
    )
    assert "reintenta" in response.json()["detail"]
    assert published == [ORDER_ID], "la etapa sí cambió: el dashboard se refresca igual"


@pytest.mark.parametrize(("method", "path", "body", "stage"), ENDPOINTS)
def test_started_emit_answers_200_after_the_start(make_api, method, path, body, stage):
    temporal = _TemporalClient()
    http, published = make_api(stage, temporal)

    response = getattr(http, method)(path, json=body)

    assert response.status_code == 200
    assert response.json()["success"] is True
    [start] = temporal.starts
    assert start["workflow"] == "EmitOrderStageWorkflow"
    assert start["id"] == f"order-stage-changed-{ORDER_ID}-{stage}"
    assert published == [ORDER_ID]


def test_emit_already_in_flight_is_not_an_error(make_api):
    """Mismo (pedido, etapa) ya en vuelo: Temporal lo dedupea por id — 200."""
    temporal = _TemporalClient(start_raises=WorkflowAlreadyStartedError("order-stage-changed", "Emit"))
    http, _ = make_api("ready", temporal)

    response = http.patch(f"/api/orders/orders/{ORDER_ID}/stage", json={"stage": "ready"})

    assert response.status_code == 200
