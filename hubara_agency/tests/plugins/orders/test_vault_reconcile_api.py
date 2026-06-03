"""Tests de los endpoints POST /vault-orders/{session}/{audit}/retry|resolve.

Parchea `WORKSPACE_VAULT_DIR` a un tmp y `get_order_registration_port` a un
FakePort para no tocar Medusa.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.orders.port import OrderRegistrationResult


@dataclass
class FakePort:
    result: OrderRegistrationResult
    calls: list = field(default_factory=list)

    async def register_order(self, **kwargs) -> OrderRegistrationResult:
        self.calls.append(kwargs)
        return self.result


def _write(vault: Path, session: str, data: dict) -> None:
    d = vault / session
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(data), encoding="utf-8")


def _failed(order_id: str = "AUDIT-1") -> dict:
    return {
        "order_id": order_id, "provider": "medusa", "success": False,
        "items": [{"handle": "vela", "quantity": 1, "unit_price_cop": 10000}],
        "shipping": {"city": "Bogotá", "neighborhood": "Centro",
                     "address": "Calle 1", "phone": "+57300"},
        "payment_method": "transfer", "subtotal_cop": 10000, "shipping_cop": 0,
        "total_cop": 10000, "currency": "COP",
        "registered_at_ms": 1779000000000, "status": "pending",
    }


@pytest.fixture
def app(tmp_path, monkeypatch):
    import src.plugins.orders.api as orders_api

    monkeypatch.setattr(orders_api, "WORKSPACE_VAULT_DIR", tmp_path)
    port = FakePort(
        result=OrderRegistrationResult(
            success=True, order_id="draft_NEW", provider="medusa",
        )
    )
    monkeypatch.setattr(orders_api, "get_order_registration_port", lambda: port)

    application = FastAPI()
    application.include_router(orders_api.router, prefix="/api/orders")
    return application, tmp_path, port


def test_retry_resolves_pending_and_removes_from_banner(app):
    application, vault, port = app
    _write(vault, "wa_1", {"failed_order_registrations": [_failed("AUDIT-1")]})
    client = TestClient(application)

    resp = client.post("/api/orders/vault-orders/wa_1/AUDIT-1/retry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "resolved"
    assert body["resolved_order_id"] == "draft_NEW"
    assert len(port.calls) == 1

    # Tras resolver, desaparece del banner.
    listing = client.get("/api/orders/vault-orders").json()
    assert listing["count"] == 0


def test_retry_404_when_not_found(app):
    application, vault, port = app
    _write(vault, "wa_1", {"failed_order_registrations": []})
    client = TestClient(application)
    resp = client.post("/api/orders/vault-orders/wa_1/AUDIT-NOPE/retry")
    assert resp.status_code == 404
    assert len(port.calls) == 0


def test_retry_is_idempotent(app):
    application, vault, port = app
    _write(vault, "wa_1", {"failed_order_registrations": [_failed("AUDIT-1")]})
    client = TestClient(application)

    r1 = client.post("/api/orders/vault-orders/wa_1/AUDIT-1/retry")
    r2 = client.post("/api/orders/vault-orders/wa_1/AUDIT-1/retry")
    assert r1.json()["outcome"] == "resolved"
    assert r2.json()["outcome"] == "already_resolved"
    assert len(port.calls) == 1  # el segundo NO tocó Medusa


def test_resolve_marks_manual_without_touching_port(app):
    application, vault, port = app
    _write(vault, "wa_1", {"failed_order_registrations": [_failed("AUDIT-1")]})
    client = TestClient(application)

    resp = client.post(
        "/api/orders/vault-orders/wa_1/AUDIT-1/resolve",
        json={"note": "registrado a mano", "resolved_order_id": "order_X"},
    )
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "resolved"
    assert len(port.calls) == 0  # resolución manual no toca Medusa

    listing = client.get("/api/orders/vault-orders").json()
    assert listing["count"] == 0


def test_resolve_404_when_not_found(app):
    application, vault, port = app
    _write(vault, "wa_1", {"failed_order_registrations": []})
    client = TestClient(application)
    resp = client.post("/api/orders/vault-orders/wa_1/AUDIT-X/resolve", json={})
    assert resp.status_code == 404
