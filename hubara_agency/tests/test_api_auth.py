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


@pytest.fixture
def prod_no_cognito(monkeypatch, main_app) -> TestClient:
    """Producción SIN Cognito configurado → debe FAIL-CLOSED, no no-op."""
    from src.platform import config

    monkeypatch.setattr(config, "COGNITO_USER_POOL_ID", "")
    monkeypatch.setattr(config, "COGNITO_APP_CLIENT_ID", "")
    monkeypatch.setattr(config, "HUBARA_ENV", "production")
    return TestClient(main_app)


def test_missing_cognito_in_production_fails_closed(
    prod_no_cognito: TestClient,
) -> None:
    """SEC-01: en prod, faltar COGNITO_* NO puede degradar a no-op.

    El incidente (deployment_live_aws: "API SIN auth") fue exactamente esto:
    la API sirvió PII de clientes sin token porque las vars nunca se
    provisionaron. En prod, config de auth ausente → la ruta del dashboard
    REHÚSA servir (503), no abre la puerta.
    """
    resp = prod_no_cognito.get("/api/dashboard/sessions")
    assert resp.status_code == 503


def test_placeholder_service_token_does_not_bypass(
    prod_no_cognito: TestClient, monkeypatch
) -> None:
    """El placeholder de Terraform (`PLACEHOLDER_set_out_of_band`, valor CONOCIDO
    en el repo) NO puede funcionar como service token — sería un bypass total de
    Cognito con una credencial pública. Debe tratarse como AUSENTE → fail-closed.
    """
    from src.platform import config

    monkeypatch.setattr(config, "HUBARA_SERVICE_TOKEN", config.SSM_PLACEHOLDER)
    resp = prod_no_cognito.get(
        "/api/dashboard/sessions",
        headers={"Authorization": f"Bearer {config.SSM_PLACEHOLDER}"},
    )
    assert resp.status_code == 503  # fail-closed, NO bypass


def test_service_token_via_query_string_is_rejected(
    prod_no_cognito: TestClient, monkeypatch
) -> None:
    """Secreto-en-URL: el service token (credencial M2M de larga vida) NO se
    acepta por query string — quedaría en access logs / proxies / referrer.
    Solo por header ``Authorization: Bearer`` (los callers reales —
    order_sentinel, post_sale_return — usan header). El ``?access_token=`` sigue
    siendo válido SOLO para el JWT de Cognito del SSE (otro camino).
    """
    from src.platform import config

    monkeypatch.setattr(config, "HUBARA_SERVICE_TOKEN", "svc-secret-123")
    resp = prod_no_cognito.get(
        "/api/dashboard/sessions", params={"access_token": "svc-secret-123"}
    )
    assert resp.status_code == 503  # el query token NO autentica al servicio


def test_service_token_still_works_in_production(
    prod_no_cognito: TestClient, monkeypatch
) -> None:
    """El fail-closed NO debe romper el service-token M2M (workers → API).

    Si HUBARA_SERVICE_TOKEN está configurado, un caller con ese bearer pasa
    aunque Cognito no esté configurado — el worker no porta identidad Cognito.
    """
    from src.platform import config

    monkeypatch.setattr(config, "HUBARA_SERVICE_TOKEN", "svc-secret-123")
    resp = prod_no_cognito.get(
        "/api/dashboard/sessions",
        headers={"Authorization": "Bearer svc-secret-123"},
    )
    assert resp.status_code != 503
    assert resp.status_code != 401
