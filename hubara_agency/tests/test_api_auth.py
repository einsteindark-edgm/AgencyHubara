"""Auth de la API (JWT/Cognito) — workstream B, PENDING_IMPLEMENTATION §2.

Comportamiento exigido:
- Rutas del DASHBOARD (PII de clientes, acciones de operador) requieren un JWT
  válido de Cognito → sin/invalid token = ``401``; con token válido pasa.
- El webhook de Meta (``/api/webhook``) NO lleva JWT de Cognito: lo llama Meta con
  su propia auth (``hub.verify_token`` + ``X-Hub-Signature-256``). Sigue PÚBLICO
  AUNQUE Cognito esté enforced — ponerle auth global rompería los mensajes.
- ``GET /`` (health/liveness, lo pega Caddy/monitoring) queda público.
- Sin Cognito configurado (dev local / tests existentes) la auth es NO-OP.

El JWKS/firma se aíslan mockeando ``src.platform.auth._verify_token`` — sin red.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def main_app():
    # `from src.main import app` dispara el auto-registro de routers al import.
    from src.main import app

    return app


@pytest.fixture
def enforced(monkeypatch, main_app) -> TestClient:
    """Cognito configurado → la auth se enforcea (modo prod)."""
    from src.platform import config

    monkeypatch.setattr(config, "COGNITO_USER_POOL_ID", "us-west-2_TESTPOOL")
    monkeypatch.setattr(config, "COGNITO_APP_CLIENT_ID", "test-app-client")
    monkeypatch.setattr(config, "AWS_REGION", "us-west-2")
    return TestClient(main_app)


def test_dashboard_route_requires_auth_without_token(enforced: TestClient) -> None:
    """Sin Authorization, una ruta del dashboard (sirve PII) debe dar 401."""
    resp = enforced.get("/api/dashboard/sessions")
    assert resp.status_code == 401


def test_dashboard_route_rejects_invalid_token(
    enforced: TestClient, monkeypatch
) -> None:
    """Token presente pero inválido (firma/exp/issuer) → 401."""

    def _reject(_token: str):
        raise ValueError("firma inválida")

    monkeypatch.setattr("src.platform.auth._verify_token", _reject)
    resp = enforced.get(
        "/api/dashboard/sessions", headers={"Authorization": "Bearer garbage"}
    )
    assert resp.status_code == 401


def test_dashboard_route_accepts_valid_token(
    enforced: TestClient, monkeypatch
) -> None:
    """Con un access-token válido de Cognito, la ruta pasa (no 401)."""
    monkeypatch.setattr(
        "src.platform.auth._verify_token",
        lambda _token: {
            "sub": "u1",
            "client_id": "test-app-client",
            "token_use": "access",
        },
    )
    resp = enforced.get(
        "/api/dashboard/sessions", headers={"Authorization": "Bearer good"}
    )
    assert resp.status_code != 401


def test_dashboard_route_accepts_token_via_query_param(
    enforced: TestClient, monkeypatch
) -> None:
    """SSE: el `EventSource` del browser no manda header Authorization, así que el
    token viaja por query param `access_token`. require_auth debe aceptarlo."""
    monkeypatch.setattr(
        "src.platform.auth._verify_token",
        lambda _token: {
            "sub": "u1",
            "client_id": "test-app-client",
            "token_use": "access",
        },
    )
    resp = enforced.get("/api/dashboard/sessions", params={"access_token": "good"})
    assert resp.status_code != 401


def test_meta_webhook_stays_public_even_when_enforced(enforced: TestClient) -> None:
    """El webhook de Meta tiene su propia auth; NO debe exigir JWT de Cognito."""
    resp = enforced.get(
        "/api/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "x",
            "hub.challenge": "123",
        },
    )
    assert resp.status_code != 401


def test_health_root_stays_public(enforced: TestClient) -> None:
    """`GET /` (liveness) queda público — lo consulta Caddy/monitoring."""
    assert enforced.get("/").status_code == 200


def test_no_cognito_config_is_noop(main_app) -> None:
    """Sin Cognito configurado → auth no-op (dev local / tests existentes ok)."""
    client = TestClient(main_app)
    assert client.get("/api/dashboard/sessions").status_code != 401
