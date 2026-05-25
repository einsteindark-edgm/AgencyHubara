"""Tests del router FastAPI del plugin `orders` (`/api/orders/orders`).

Inyectamos un FakeOrderQueryPort via `app.dependency_overrides`-equivalent
(monkeypatch sobre el composition factory) para evitar tocar Medusa.

Cubrimos:
  1. GET /orders → lista con shape JSON correcto.
  2. GET /orders cuando catalog_available=False → status 200 con orders=[]
     y campo error_detail.
  3. GET /orders/{id} → detalle.
  4. GET /orders/{id} → 404 cuando port.get() devuelve None.
  5. GET /orders-health.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.orders.query_port import (
    OrderAddressDTO,
    OrderDetailDTO,
    OrderItemDTO,
    OrderListDTO,
    OrderSummaryDTO,
    OrderTimelineEventDTO,
)


# ----------------------------------------------------------------------
# Fake port
# ----------------------------------------------------------------------


def _sample_summary(**overrides) -> OrderSummaryDTO:
    base = dict(
        id="order_01HX",
        display_id="#1247",
        customer="María Camila",
        short="MC",
        color="a",
        phone="3001234567",
        city="Bogotá",
        channel="WhatsApp",
        status="preparing",
        pay_status="paid",
        pay_type="confirmed",
        items=2,
        pieces=3,
        total_cop=124500,
        currency_code="COP",
        is_draft=False,
        due_iso="2026-05-23",
        due_time="—",
        overdue=False,
        priority="normal",
        agent="—",
        created_at_ms=1779800400000,
        updated_at_ms=1779800400000,
    )
    base.update(overrides)
    return OrderSummaryDTO(**base)


def _sample_detail(summary: OrderSummaryDTO) -> OrderDetailDTO:
    return OrderDetailDTO(
        summary=summary,
        items_detail=[
            OrderItemDTO(
                title="Vela Sagrado Corazón",
                sku="VEL-001",
                quantity=2,
                unit_price_cop=17000,
                total_cop=34000,
                handle="vela-sagrado-corazon",
            )
        ],
        shipping_address=OrderAddressDTO(
            first_name="María", last_name="Camila", phone="3001234567",
            address_1="Calle 100", address_2="Chapinero", city="Bogotá", country_code="co",
        ),
        billing_address=None,
        subtotal_cop=119500,
        shipping_cop=5000,
        tax_total_cop=0,
        discount_total_cop=0,
        timeline=[
            OrderTimelineEventDTO(
                type="created", label="Pedido creado",
                timestamp_ms=1779800400000, detail=None,
            )
        ],
        payment_method_label="Transferencia",
        notes=[],
        data_completeness_missing=["due_date", "agent", "notes"],
    )


@dataclass
class FakeOrderQueryPort:
    list_result: OrderListDTO = field(
        default_factory=lambda: OrderListDTO(
            orders=[_sample_summary()],
            count=1, offset=0, limit=100,
            catalog_available=True, error_detail=None,
        )
    )
    detail_result: OrderDetailDTO | None = field(
        default_factory=lambda: _sample_detail(_sample_summary())
    )

    async def list(
        self, *, limit: int = 50, offset: int = 0, include_drafts: bool = True
    ) -> OrderListDTO:
        return self.list_result

    async def get(self, order_id: str) -> OrderDetailDTO | None:
        return self.detail_result


@pytest.fixture
def app_with_fake_port(monkeypatch):
    """Build a fresh FastAPI app with the orders router and a fake port."""
    fake = FakeOrderQueryPort()
    # Patch the composition factory at the import site (NOT the source).
    # The router calls `get_order_query_port()` at request time, so we just
    # need to make sure that lookup returns our fake.
    monkeypatch.setattr(
        "src.plugins.orders.api.get_order_query_port",
        lambda: fake,
    )
    from src.plugins.orders import api as orders_api
    app = FastAPI()
    app.include_router(orders_api.router, prefix="/api/orders")
    return app, fake


def test_list_orders_returns_shape(app_with_fake_port):
    app, _ = app_with_fake_port
    client = TestClient(app)
    response = client.get("/api/orders/orders")
    assert response.status_code == 200
    body = response.json()
    assert body["catalog_available"] is True
    assert body["count"] == 1
    assert body["limit"] == 100
    assert len(body["orders"]) == 1
    o = body["orders"][0]
    assert o["id"] == "order_01HX"
    assert o["display_id"] == "#1247"
    assert o["customer"] == "María Camila"
    assert o["short"] == "MC"
    assert o["status"] == "preparing"
    assert o["pay_status"] == "paid"
    assert o["pay_type"] == "confirmed"
    assert o["total_cop"] == 124500
    assert o["is_draft"] is False


def test_list_orders_when_medusa_unavailable_returns_empty(app_with_fake_port, monkeypatch):
    app, fake = app_with_fake_port
    fake.list_result = OrderListDTO(
        orders=[], count=0, offset=0, limit=100,
        catalog_available=False,
        error_detail="medusa_api_error: HTTP 503 /admin/orders",
    )
    client = TestClient(app)
    response = client.get("/api/orders/orders")
    assert response.status_code == 200
    body = response.json()
    assert body["catalog_available"] is False
    assert body["orders"] == []
    assert "503" in body["error_detail"]


def test_get_order_detail_returns_shape(app_with_fake_port):
    app, _ = app_with_fake_port
    client = TestClient(app)
    response = client.get("/api/orders/orders/order_01HX")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["id"] == "order_01HX"
    assert body["summary"]["customer"] == "María Camila"
    assert len(body["items_detail"]) == 1
    assert body["items_detail"][0]["title"] == "Vela Sagrado Corazón"
    assert body["subtotal_cop"] == 119500
    assert body["shipping_cop"] == 5000
    assert body["payment_method_label"] == "Transferencia"
    assert "due_date" in body["data_completeness_missing"]


def test_get_order_detail_404_when_port_returns_none(app_with_fake_port):
    app, fake = app_with_fake_port
    fake.detail_result = None
    client = TestClient(app)
    response = client.get("/api/orders/orders/order_missing")
    assert response.status_code == 404
    assert "order_missing" in response.json()["detail"]


def test_orders_health(app_with_fake_port):
    app, _ = app_with_fake_port
    client = TestClient(app)
    response = client.get("/api/orders/orders-health")
    assert response.status_code == 200
    body = response.json()
    assert body["port"] == "FakeOrderQueryPort"
    assert body["catalog_available"] is True
    assert body["sample_count"] == 1
