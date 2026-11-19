"""Auth M2M acotada por scope (ADR 2026-10-14 agent-gateway).

El Hubara Agent Gateway llama al backend con un access token ``client_credentials``
PROPIO (app client M2M de Cognito), nunca con el token del usuario ni con el
``HUBARA_SERVICE_TOKEN``. ``require_auth`` lo acepta SOLO si:
- su ``client_id`` está en ``COGNITO_M2M_CLIENT_IDS``,
- trae el scope ``hubara-api/ads.read``,
- y el request es ``GET`` bajo ``/api/ads/``.
Cualquier otra ruta/método con ese token → 403. Un client no listado → 401.

Firma real (RSA local) + JWKS mockeado: ejercita el ``_verify_token`` de verdad.
"""
from __future__ import annotations

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

POOL = "us-west-2_TESTPOOL"
ISSUER = f"https://cognito-idp.us-west-2.amazonaws.com/{POOL}"
DASHBOARD = "test-app-client"
GATEWAY = "agent-gateway-client"
READ = "hubara-api/ads.read"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _SigningKey:
    key = _KEY.public_key()


class _FakeJwks:
    def get_signing_key_from_jwt(self, _token: str) -> _SigningKey:
        return _SigningKey()


def _token(**overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "sub": GATEWAY,
        "iss": ISSUER,
        "client_id": GATEWAY,
        "token_use": "access",
        "scope": READ,
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    return jwt.encode(claims, _KEY, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture
def api(monkeypatch) -> TestClient:
    from src.main import app
    from src.platform import auth, config

    monkeypatch.setattr(config, "COGNITO_USER_POOL_ID", POOL)
    monkeypatch.setattr(config, "COGNITO_APP_CLIENT_ID", DASHBOARD)
    monkeypatch.setattr(config, "COGNITO_M2M_CLIENT_IDS", GATEWAY)
    monkeypatch.setattr(config, "AWS_REGION", "us-west-2")
    monkeypatch.setattr(auth, "_jwk_client", lambda _uri: _FakeJwks())
    return TestClient(app)


def _get(api: TestClient, path: str, token: str, **headers: str):
    return api.get(path, headers={"Authorization": f"Bearer {token}", **headers})


def test_m2m_with_ads_read_scope_can_get_ads_routes(api: TestClient) -> None:
    resp = _get(api, "/api/ads/analysis/agents", _token())

    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_m2m_cannot_write_even_under_ads(api: TestClient) -> None:
    resp = api.post(
        "/api/ads/analysis/runs",
        json={"agent": "ads-analytics", "input": "x"},
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert resp.status_code == 403


def test_m2m_cannot_read_outside_ads(api: TestClient) -> None:
    assert _get(api, "/api/dashboard/sessions", _token()).status_code == 403


def test_m2m_without_ads_read_scope_is_forbidden(api: TestClient) -> None:
    assert _get(api, "/api/ads/analysis/agents", _token(scope="hubara-api/other")).status_code == 403


def test_unlisted_m2m_client_is_rejected(api: TestClient) -> None:
    token = _token(client_id="some-other-m2m", sub="some-other-m2m")

    assert _get(api, "/api/ads/analysis/agents", token).status_code == 401


def test_dsh_plugin_user_token_is_not_accepted_by_backend(api: TestClient) -> None:
    """El token del usuario emitido para el gateway (client dsh-hubara) no sirve acá."""
    token = _token(client_id="dsh-hubara-client", sub="user-1", scope=f"openid hubara-mcp/ads.read {READ}")

    assert _get(api, "/api/ads/analysis/agents", token).status_code == 401


def test_dashboard_user_token_still_works_everywhere(api: TestClient) -> None:
    token = _token(client_id=DASHBOARD, sub="user-1", username="user-1", scope="openid email profile")

    assert _get(api, "/api/ads/analysis/agents", token).status_code == 200


def test_m2m_actor_is_the_client_not_the_on_behalf_of_header(api: TestClient) -> None:
    from src.platform.auth import current_actor
    from starlette.requests import Request

    from src.platform import auth

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/ads/analysis/agents",
        "headers": [
            (b"authorization", f"Bearer {_token()}".encode()),
            (b"x-hubara-on-behalf-of", b"user-sub-1"),
        ],
        "query_string": b"",
    }
    request = Request(scope)

    auth.require_auth(request)

    assert current_actor(request) == f"m2m:{GATEWAY}"


def test_m2m_disabled_when_allowlist_empty(api: TestClient, monkeypatch) -> None:
    from src.platform import config

    monkeypatch.setattr(config, "COGNITO_M2M_CLIENT_IDS", "")

    assert _get(api, "/api/ads/analysis/agents", _token()).status_code == 401


# ── Bordes (revisión 2026-10-14) ──────────────────────────────────────────


def test_m2m_host_header_cannot_smuggle_an_ads_path(api: TestClient) -> None:
    """B1: `request.url.path` se arma con el Host; la decisión va sobre la ruta que enruta."""
    resp = _get(api, "/api/dashboard/sessions", _token(), Host="x/api/ads")

    assert resp.status_code == 403


@pytest.mark.parametrize(
    "path",
    [
        "/api/ads/meta/insights",  # Graph en vivo con el token de Meta (costo/rate limit)
        "/api/ads/meta/analysis-input",  # pagina órdenes de Medusa
        "/api/ads/analysis/runs/run-000000000000/events",  # SSE
    ],
)
def test_m2m_ads_read_only_reaches_the_gateway_catalog(api: TestClient, path: str) -> None:
    """I1: el scope autoriza las 11 rutas del gateway, no todo el prefijo /api/ads/."""
    assert _get(api, path, _token()).status_code == 403


def test_m2m_token_by_query_string_is_rejected(api: TestClient) -> None:
    """M1: una credencial M2M nunca viaja en la URL (logs/proxies)."""
    resp = api.get(f"/api/ads/analysis/agents?access_token={_token()}")

    assert resp.status_code == 401
