"""POST /api/orders/orders/{id}/adjust-prices — «Ajustar precio» del inspector.

Fija: el body (`prices`, `reason`) llega al command; quién lo hizo sale de la
identidad verificada del request (nunca del body: es un cambio de dinero); el
éxito publica el SSE de orders y la respuesta tiene el shape común.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.platform.orders.command_port import AdjustPricesCommand, LinePrice, OrderCommandResult


class FakePort:
    def __init__(self, result: OrderCommandResult) -> None:
        self.result = result
        self.commands: list = []

    async def adjust_prices(self, command) -> OrderCommandResult:
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

    @app.middleware("http")
    async def _verified_actor(request: Request, call_next):  # lo que deja `require_auth`
        request.state.hubara_actor = "operadora@hubara.co"
        return await call_next(request)

    app.include_router(orders_api.router, prefix="/api/orders")
    return TestClient(app), port, published


def test_adjust_prices_forwards_prices_reason_and_verified_actor(monkeypatch) -> None:
    client, port, published = _client(
        monkeypatch, OrderCommandResult(success=True, order_id="#169", current_stage="delivered")
    )
    resp = client.post(
        "/api/orders/orders/%23169/adjust-prices",
        json={
            "prices": [
                {"item_id": "ordli_ond", "unit_price_cop": 18000},
                {"item_id": "ordli_cua", "unit_price_cop": 20000},
            ],
            "reason": "Precio por mayor negociado",
            "by": "alguien-del-body",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "success": True,
        "order_id": "#169",
        "current_stage": "delivered",
        "error_detail": None,
        "audit_id": None,
    }
    assert port.commands == [
        AdjustPricesCommand(
            order_id="#169",
            prices=(
                LinePrice(item_id="ordli_ond", unit_price_cop=18000),
                LinePrice(item_id="ordli_cua", unit_price_cop=20000),
            ),
            reason="Precio por mayor negociado",
            by="operadora@hubara.co",
        )
    ]
    assert published == ["#169"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"prices": []},
        {"prices": "18000"},
        {"prices": [{"unit_price_cop": 18000}]},
        {"prices": [{"item_id": "", "unit_price_cop": 18000}]},
    ],
)
def test_adjust_prices_rejects_a_malformed_body(monkeypatch, body) -> None:
    client, port, published = _client(monkeypatch, OrderCommandResult(success=True, order_id="o1"))
    resp = client.post("/api/orders/orders/o1/adjust-prices", json=body)
    assert resp.status_code == 422
    assert port.commands == []
    assert published == []


def test_adjust_prices_failure_does_not_publish(monkeypatch) -> None:
    client, port, published = _client(
        monkeypatch,
        OrderCommandResult(success=False, order_id="o1", error_detail="validation_error: x"),
    )
    resp = client.post(
        "/api/orders/orders/o1/adjust-prices",
        json={"prices": [{"item_id": "l1", "unit_price_cop": 1000}], "reason": ""},
    )
    assert resp.json()["error_detail"] == "validation_error: x"
    assert port.commands[0].reason == ""
    assert published == []
