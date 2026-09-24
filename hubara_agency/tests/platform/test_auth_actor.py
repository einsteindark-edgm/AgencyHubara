"""Actor VERIFICADO de la sesión (Fase 7.0 de CUPONES_PLAN.md, D6).

La central de cupones no tiene roles: cualquier usuario del dashboard crea y
edita cupones, así que cada acción se registra con QUIÉN la hizo. Ese quién
sale de la auth que ya pasó (`require_auth`), nunca del cuerpo del request.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.platform import auth, config


def _request(bearer: str | None = None) -> SimpleNamespace:
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    return SimpleNamespace(
        headers=headers,
        query_params={},
        url=SimpleNamespace(path="/api/marketing/coupons"),
        state=SimpleNamespace(),
    )


@pytest.fixture
def _cognito_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "COGNITO_USER_POOL_ID", "pool-1")
    monkeypatch.setattr(config, "COGNITO_APP_CLIENT_ID", "client-1")
    monkeypatch.setattr(config, "HUBARA_SERVICE_TOKEN", "secreto-interno-123")


def test_require_auth_sets_verified_actor_from_cognito_claims(_cognito_on, monkeypatch) -> None:
    monkeypatch.setattr(
        auth, "_verify_token",
        lambda token: {"username": "ana.perez", "sub": "uuid-1", "client_id": "client-1"},
    )
    request = _request("jwt-de-ana")

    auth.require_auth(request)

    assert request.state.hubara_actor == "ana.perez"
    assert auth.current_actor(request) == "ana.perez"


def test_cognito_actor_falls_back_to_sub(_cognito_on, monkeypatch) -> None:
    monkeypatch.setattr(auth, "_verify_token", lambda token: {"sub": "uuid-1"})
    request = _request("jwt")

    auth.require_auth(request)

    assert auth.current_actor(request) == "uuid-1"


def test_service_token_actor_is_service(_cognito_on) -> None:
    request = _request("secreto-interno-123")

    auth.require_auth(request)

    assert auth.current_actor(request) == "service"


def test_dev_without_cognito_actor_is_local(monkeypatch) -> None:
    monkeypatch.setattr(config, "COGNITO_USER_POOL_ID", "")
    monkeypatch.setattr(config, "COGNITO_APP_CLIENT_ID", "")
    monkeypatch.setattr(config, "is_production", lambda: False)
    request = _request()

    auth.require_auth(request)

    assert auth.current_actor(request) == "local"


def test_actor_without_auth_is_unknown_never_invented() -> None:
    # Una ruta montada sin `require_auth` no tiene actor: no se inventa uno.
    assert auth.current_actor(_request()) == "desconocido"


def test_request_without_state_does_not_break_auth(_cognito_on) -> None:
    # Dobles viejos de request sin `state` (tests de SSE/ticket) siguen andando.
    request = SimpleNamespace(
        headers={"Authorization": "Bearer secreto-interno-123"},
        query_params={}, url=SimpleNamespace(path="/x"),
    )
    auth.require_auth(request)
    assert auth.current_actor(request) == "desconocido"
