"""`GET /api/orders/orders/lines` (tarjeta del ciclo #49): las líneas de los
pedidos para el reporte por producto. Solo lectura, con el mismo tope que la
lista y el mismo «vacío con el motivo» cuando la tienda no responde. Datos
inventados.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.orders.query_port import (
    OrderItemDTO,
    OrderLinesDTO,
    OrderLinesListDTO,
)


def _lines() -> OrderLinesDTO:
    return OrderLinesDTO(
        order_id="order_01LINES",
        display_id="#301",
        payment_method_label="Link de pago",
        items=[
            OrderItemDTO(
                title="Vela Luna",
                sku=None,
                quantity=2,
                unit_price_cop=13500,
                total_cop=27000,
                coupon_code="LUNA10",
                list_unit_price_cop=15000,
                discount_unit_cop=1500,
            )
        ],
    )


@dataclass
class FakePort:
    result: OrderLinesListDTO = field(
        default_factory=lambda: OrderLinesListDTO(
            orders=[_lines()], count=1, offset=0, limit=100, catalog_available=True, error_detail=None
        )
    )
    calls: list[dict] = field(default_factory=list)

    async def list_lines(self, *, limit: int = 50, offset: int = 0, include_drafts: bool = True):
        self.calls.append({"limit": limit, "offset": offset, "include_drafts": include_drafts})
        return self.result


@pytest.fixture
def client_and_port(monkeypatch):
    fake = FakePort()
    monkeypatch.setattr("src.plugins.orders.api.get_order_query_port", lambda: fake)
    from src.plugins.orders import api as orders_api

    app = FastAPI()
    app.include_router(orders_api.router, prefix="/api/orders")
    return TestClient(app), fake


def test_lines_route_returns_the_lines_of_each_order(client_and_port):
    client, fake = client_and_port

    response = client.get("/api/orders/orders/lines")

    assert response.status_code == 200
    body = response.json()
    assert body["catalog_available"] is True
    assert body["limit"] == 100
    [order] = body["orders"]
    assert order["display_id"] == "#301"
    assert order["payment_method_label"] == "Link de pago"
    [item] = order["items"]
    assert item["title"] == "Vela Luna"
    assert item["list_unit_price_cop"] == 15000
    assert item["discount_unit_cop"] == 1500
    # Mismo tope por defecto que la lista de Orders (100), para que las
    # líneas cubran los mismos pedidos que el reporte por pedido.
    assert fake.calls == [{"limit": 100, "offset": 0, "include_drafts": True}]


def test_lines_route_is_not_taken_by_the_order_detail_route(client_and_port):
    client, fake = client_and_port

    client.get("/api/orders/orders/lines?limit=40&offset=40")

    assert fake.calls == [{"limit": 40, "offset": 40, "include_drafts": True}]


def test_lines_route_when_the_store_fails_answers_empty_with_the_reason(client_and_port):
    client, fake = client_and_port
    fake.result = OrderLinesListDTO(
        orders=[], count=0, offset=0, limit=100, catalog_available=False,
        error_detail="medusa_unavailable: HTTP 503 /admin/orders",
    )

    response = client.get("/api/orders/orders/lines")

    assert response.status_code == 200
    body = response.json()
    assert body["orders"] == []
    assert body["catalog_available"] is False
    assert "503" in body["error_detail"]
