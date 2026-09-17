"""PATCH /api/orders/orders/{id}/reverse-payment — "Reversar pago" del inspector.

Fija: el body (`reason`, `by`) llega al command, el éxito publica el SSE de
orders (el tablero repinta) y la respuesta tiene el shape común de comandos.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.orders.command_port import OrderCommandResult, ReversePaymentCommand


class FakePort:
    def __init__(self, result: OrderCommandResult) -> None:
        self.result = result
        self.commands: list = []

    async def reverse_payment(self, command) -> OrderCommandResult:
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


def test_reverse_payment_forwards_reason_and_publishes(monkeypatch) -> None:
    client, port, published = _client(
        monkeypatch, OrderCommandResult(success=True, order_id="#32", current_stage="preparing")
    )
    resp = client.patch(
        "/api/orders/orders/%2332/reverse-payment",
        json={"reason": "confirmado por error", "by": "edgm"},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "success": True,
        "order_id": "#32",
        "current_stage": "preparing",
        "error_detail": None,
        "audit_id": None,
    }
    assert port.commands == [
        ReversePaymentCommand(order_id="#32", reason="confirmado por error", by="edgm")
    ]
    assert published == ["#32"]


@pytest.mark.parametrize("body", [{}, {"reason": "   "}, {"reason": 5}])
def test_reverse_payment_without_reason_sends_none(monkeypatch, body) -> None:
    client, port, published = _client(
        monkeypatch,
        OrderCommandResult(success=False, order_id="o1", error_detail="invalid_state: x"),
    )
    resp = client.patch("/api/orders/orders/o1/reverse-payment", json=body)
    assert resp.json()["error_detail"] == "invalid_state: x"
    assert port.commands[0].reason is None
    assert port.commands[0].by == "human"
    assert published == []
