"""Tests del `vault_scanner` y endpoint `/api/orders/vault-orders`.

Cubre el premortem F2+K1: pedidos que el agente cerró pero NO están en
Medusa (failed registrations + stub orders) deben ser visibles en el
dashboard para que el operador no los pierda.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.plugins.orders.vault_scanner import scan_vault_orders


def _write_metadata(vault: Path, session_key: str, data: dict) -> None:
    session_dir = vault / session_key
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "metadata.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def test_scan_empty_vault_returns_empty_list(tmp_path):
    assert scan_vault_orders(tmp_path) == []


def test_scan_nonexistent_vault_returns_empty_list(tmp_path):
    assert scan_vault_orders(tmp_path / "no-such-dir") == []


def test_scan_extracts_failed_registrations(tmp_path):
    _write_metadata(tmp_path, "wa_57311", {
        "failed_order_registrations": [
            {
                "order_id": "AUDIT-wa_57311-1779800-abc",
                "provider": "medusa",
                "success": False,
                "error_detail": "medusa_api_error: HTTP 503",
                "items": [{"handle": "vela-x", "quantity": 1, "unit_price_cop": 17000}],
                "shipping": {
                    "phone": "+57311",
                    "city": "Bogotá",
                    "neighborhood": "Chapinero",
                    "address": "Calle 100",
                },
                "total_cop": 17000,
                "currency": "COP",
                "payment_method": "transfer",
                "registered_at_ms": 1779800400000,
            }
        ],
    })

    records = scan_vault_orders(tmp_path)
    assert len(records) == 1
    r = records[0]
    assert r.kind == "failed"
    assert r.session_key == "wa_57311"
    assert r.order_id == "AUDIT-wa_57311-1779800-abc"
    assert r.customer_phone == "+57311"
    assert r.customer_city == "Bogotá"
    assert r.total_cop == 17000
    assert r.items_count == 1
    assert r.payment_method == "transfer"
    assert r.error_detail == "medusa_api_error: HTTP 503"
    assert r.registered_at_ms == 1779800400000


def test_scan_extracts_stub_orders(tmp_path):
    _write_metadata(tmp_path, "wa_stub", {
        "registered_order": {
            "order_id": "HUB-wa_stub-1779-xyz",
            "provider": "stub",
            "success": True,
            "items": [
                {"handle": "v1", "quantity": 1, "unit_price_cop": 10000},
                {"handle": "v2", "quantity": 2, "unit_price_cop": 5000},
            ],
            "shipping": {"phone": "+57322", "city": "Medellín", "neighborhood": "El Poblado", "address": "Cra 33"},
            "total_cop": 20000,
            "currency": "COP",
            "payment_method": "cash_on_delivery",
            "registered_at_ms": 1779700000000,
        },
    })

    records = scan_vault_orders(tmp_path)
    assert len(records) == 1
    assert records[0].kind == "stub"
    assert records[0].order_id == "HUB-wa_stub-1779-xyz"
    assert records[0].customer_city == "Medellín"
    assert records[0].items_count == 2
    assert records[0].payment_method == "cash_on_delivery"


def test_scan_skips_medusa_orders(tmp_path):
    """Si `registered_order` tiene `provider=medusa`, NO debe aparecer en
    el scan — esa orden ya está en Medusa y el dashboard la lee desde ahí."""
    _write_metadata(tmp_path, "wa_medusa", {
        "registered_order": {
            "order_id": "draft_01HXX",
            "provider": "medusa",
            "success": True,
            "items": [],
            "shipping": {},
            "total_cop": 5000,
            "currency": "COP",
            "payment_method": "transfer",
            "registered_at_ms": 1779000000000,
        },
    })

    records = scan_vault_orders(tmp_path)
    assert records == []


def test_scan_handles_mixed_failed_and_stub(tmp_path):
    """Sesión con un éxito stub + 2 fallos: debe surface los 3."""
    _write_metadata(tmp_path, "wa_mix", {
        "registered_order": {
            "order_id": "HUB-mix-001", "provider": "stub", "success": True,
            "items": [], "shipping": {}, "total_cop": 5000, "currency": "COP",
            "payment_method": "transfer", "registered_at_ms": 1779100000000,
        },
        "failed_order_registrations": [
            {
                "order_id": "AUDIT-mix-1", "provider": "medusa", "success": False,
                "items": [], "shipping": {}, "total_cop": 3000, "currency": "COP",
                "payment_method": "card", "registered_at_ms": 1779200000000,
                "error_detail": "first fail",
            },
            {
                "order_id": "AUDIT-mix-2", "provider": "medusa", "success": False,
                "items": [], "shipping": {}, "total_cop": 4000, "currency": "COP",
                "payment_method": "card", "registered_at_ms": 1779300000000,
                "error_detail": "second fail",
            },
        ],
    })

    records = scan_vault_orders(tmp_path)
    assert len(records) == 3
    # Ordenados desc por timestamp.
    assert records[0].order_id == "AUDIT-mix-2"
    assert records[1].order_id == "AUDIT-mix-1"
    assert records[2].order_id == "HUB-mix-001"


def test_scan_orders_by_timestamp_desc(tmp_path):
    """Registros se devuelven más reciente primero — multi-sesión."""
    _write_metadata(tmp_path, "wa_old", {
        "registered_order": {
            "order_id": "HUB-old", "provider": "stub", "success": True,
            "items": [], "shipping": {}, "total_cop": 0, "currency": "COP",
            "payment_method": "transfer", "registered_at_ms": 1779000000000,
        },
    })
    _write_metadata(tmp_path, "wa_new", {
        "registered_order": {
            "order_id": "HUB-new", "provider": "stub", "success": True,
            "items": [], "shipping": {}, "total_cop": 0, "currency": "COP",
            "payment_method": "transfer", "registered_at_ms": 1779900000000,
        },
    })

    records = scan_vault_orders(tmp_path)
    assert [r.order_id for r in records] == ["HUB-new", "HUB-old"]


def test_scan_skips_corrupt_metadata(tmp_path):
    """metadata.json corrupto → log warning + skip, NO levanta."""
    session_dir = tmp_path / "wa_corrupt"
    session_dir.mkdir()
    (session_dir / "metadata.json").write_text("{ not json", encoding="utf-8")

    # Otra sesión válida.
    _write_metadata(tmp_path, "wa_ok", {
        "registered_order": {
            "order_id": "HUB-ok", "provider": "stub", "success": True,
            "items": [], "shipping": {}, "total_cop": 0, "currency": "COP",
            "payment_method": "transfer", "registered_at_ms": 1779000000000,
        },
    })

    records = scan_vault_orders(tmp_path)
    assert len(records) == 1
    assert records[0].order_id == "HUB-ok"


def test_scan_skips_non_wa_directories(tmp_path):
    """Directorios sin prefix `wa_` (e.g. `_analytics`) deben skipearse."""
    (tmp_path / "_analytics").mkdir()
    (tmp_path / "_analytics" / "metadata.json").write_text("{}", encoding="utf-8")
    _write_metadata(tmp_path, "wa_real", {
        "registered_order": {
            "order_id": "HUB-real", "provider": "stub", "success": True,
            "items": [], "shipping": {}, "total_cop": 0, "currency": "COP",
            "payment_method": "transfer", "registered_at_ms": 1779000000000,
        },
    })

    records = scan_vault_orders(tmp_path)
    assert len(records) == 1
    assert records[0].session_key == "wa_real"


# ----------------------------------------------------------------------
# Endpoint test
# ----------------------------------------------------------------------


@pytest.fixture
def app_with_temp_vault(tmp_path, monkeypatch):
    """FastAPI app que apunta a un vault tmp en lugar del real."""
    import src.plugins.orders.api as orders_api
    from fastapi import FastAPI

    monkeypatch.setattr(orders_api, "WORKSPACE_VAULT_DIR", tmp_path)
    # También necesitamos parchear el port para no necesitar Medusa.
    from dataclasses import dataclass

    @dataclass
    class FakePort:
        async def list(self, **_):
            from src.platform.orders.query_port import OrderListDTO
            return OrderListDTO(orders=[], count=0, offset=0, limit=100,
                                catalog_available=True, error_detail=None)

        async def get(self, _id):
            return None

    monkeypatch.setattr(orders_api, "get_order_query_port", lambda: FakePort())

    app = FastAPI()
    app.include_router(orders_api.router, prefix="/api/orders")
    return app, tmp_path


def test_vault_orders_endpoint_returns_empty_when_no_records(app_with_temp_vault):
    from fastapi.testclient import TestClient
    app, _ = app_with_temp_vault
    client = TestClient(app)
    response = client.get("/api/orders/vault-orders")
    assert response.status_code == 200
    body = response.json()
    assert body["records"] == []
    assert body["count"] == 0
    assert body["failed_count"] == 0
    assert body["stub_count"] == 0


def test_vault_orders_endpoint_returns_records(app_with_temp_vault):
    from fastapi.testclient import TestClient
    app, vault = app_with_temp_vault
    _write_metadata(vault, "wa_endpoint", {
        "registered_order": {
            "order_id": "HUB-ep", "provider": "stub", "success": True,
            "items": [{"handle": "x", "quantity": 1, "unit_price_cop": 1000}],
            "shipping": {"phone": "+57", "city": "Cali"},
            "total_cop": 1000, "currency": "COP",
            "payment_method": "transfer", "registered_at_ms": 1779000000000,
        },
        "failed_order_registrations": [
            {
                "order_id": "AUDIT-ep-1", "provider": "medusa", "success": False,
                "items": [], "shipping": {}, "total_cop": 0, "currency": "COP",
                "payment_method": "card", "registered_at_ms": 1779500000000,
                "error_detail": "down",
            }
        ],
    })

    client = TestClient(app)
    response = client.get("/api/orders/vault-orders")
    body = response.json()
    assert body["count"] == 2
    assert body["failed_count"] == 1
    assert body["stub_count"] == 1
    kinds = sorted(r["kind"] for r in body["records"])
    assert kinds == ["failed", "stub"]
