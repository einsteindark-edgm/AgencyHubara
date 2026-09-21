"""PATCH /api/orders/orders/{id}/test-order — botón "Marcar como prueba" del inspector.

Fija: el body (`is_test`, `by`) llega al command, el éxito publica el SSE de
orders (el tablero y los totales repintan) y un body sin `is_test` booleano es
un error de validación — nunca se marca/desmarca por omisión.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.orders.command_port import OrderCommandResult, SetTestOrderCommand


class FakePort:
    def __init__(self, result: OrderCommandResult) -> None:
        self.result = result
        self.commands: list = []

    async def set_test_order(self, command) -> OrderCommandResult:
        self.commands.append(command)
        return self.result


def _client(monkeypatch, result: OrderCommandResult):
    port = FakePort(result)
    published: list = []
    monkeypatch.setattr("src.plugins.orders.api.get_order_command_port", lambda: port)
    monkeypatch.setattr(
        "src.plugins.orders.api._publish_orders_changed",
        lambda order_id=None: published.append(order_id),
    )
    from src.plugins.orders import api as orders_api

    app = FastAPI()
    app.include_router(orders_api.router, prefix="/api/orders")
    return TestClient(app), port, published


@pytest.mark.parametrize("is_test", [True, False])
def test_forwards_flag_and_publishes(monkeypatch, is_test: bool) -> None:
    client, port, published = _client(
        monkeypatch, OrderCommandResult(success=True, order_id="#7", current_stage="new")
    )
    resp = client.patch(
        "/api/orders/orders/%237/test-order", json={"is_test": is_test, "by": "edgm"}
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert port.commands == [SetTestOrderCommand(order_id="#7", is_test=is_test, by="edgm")]
    assert published == ["#7"]


@pytest.mark.parametrize("body", [{}, {"is_test": "true"}, {"is_test": 1}])
def test_rejects_missing_or_non_boolean_flag(monkeypatch, body) -> None:
    client, port, published = _client(
        monkeypatch, OrderCommandResult(success=True, order_id="o1")
    )
    resp = client.patch("/api/orders/orders/o1/test-order", json=body)
    assert resp.status_code == 422
    assert port.commands == []
    assert published == []


def test_failure_does_not_publish(monkeypatch) -> None:
    client, _port, published = _client(
        monkeypatch,
        OrderCommandResult(success=False, order_id="o1", error_detail="not_found: x"),
    )
    resp = client.patch("/api/orders/orders/o1/test-order", json={"is_test": True})
    assert resp.json()["error_detail"] == "not_found: x"
    assert published == []
