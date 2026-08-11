"""Service token interno para llamadas machine-to-machine a la API (fase roja).

Incidente prod 2026-07-10 (run 019f4d63): el worker order-sentinel llama la
API de orders por HTTP y recibe 401 — `require_auth` solo acepta JWT de
Cognito, y un worker no tiene request entrante del cual portar identidad
(castkit no aplica). Riesgo anotado en el premortem (PM-019), materializado
en el primer run real con la ruta corregida.

Contrato del fix:
  * `HUBARA_SERVICE_TOKEN` (SSM/env) habilita un bearer de servicio interno.
  * `require_auth` lo acepta ADEMÁS del JWT de Cognito (comparación en
    tiempo constante). Token de servicio errado → sigue el camino Cognito
    normal (→ 401 si no valida).
  * SIN el env configurado, el comportamiento es EXACTAMENTE el actual
    (fail-closed: nadie entra con bearer arbitrario).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.platform import auth, config


def _request(bearer: str | None = None) -> SimpleNamespace:
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    # `url.path` lo lee require_auth (log del 503 + gate del ticket SSE de /events).
    return SimpleNamespace(
        headers=headers,
        query_params={},
        url=SimpleNamespace(path="/api/dashboard/sessions"),
    )


@pytest.fixture
def _cognito_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "COGNITO_USER_POOL_ID", "pool-1")
    monkeypatch.setattr(config, "COGNITO_APP_CLIENT_ID", "client-1")
    # Cognito real inaccesible en tests: todo token que llegue a la
    # verificación JWT es inválido.
    monkeypatch.setattr(
        auth, "_verify_token", lambda token: (_ for _ in ()).throw(ValueError("jwt inválido"))
    )


def test_service_token_correcto_pasa(_cognito_on, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "HUBARA_SERVICE_TOKEN", "secreto-interno-123")
    auth.require_auth(_request("secreto-interno-123"))  # no levanta


def test_service_token_errado_cae_al_camino_cognito(
    _cognito_on, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(config, "HUBARA_SERVICE_TOKEN", "secreto-interno-123")
    with pytest.raises(HTTPException) as exc:
        auth.require_auth(_request("otro-token"))
    assert exc.value.status_code == 401


def test_sin_service_token_configurado_el_comportamiento_no_cambia(
    _cognito_on, monkeypatch: pytest.MonkeyPatch
):
    """Fail-closed intacto: sin el env, un bearer arbitrario sigue siendo 401
    (nada de aceptar strings vacíos ni bypass accidental)."""
    monkeypatch.setattr(config, "HUBARA_SERVICE_TOKEN", "")
    with pytest.raises(HTTPException) as exc:
        auth.require_auth(_request("cualquier-cosa"))
    assert exc.value.status_code == 401


def test_bearer_vacio_no_matchea_token_vacio(
    _cognito_on, monkeypatch: pytest.MonkeyPatch
):
    """El caso trampa: env vacío + request sin token NUNCA pasa por el camino
    del service token ('' == '' sería bypass total)."""
    monkeypatch.setattr(config, "HUBARA_SERVICE_TOKEN", "")
    with pytest.raises(HTTPException) as exc:
        auth.require_auth(_request(None))
    assert exc.value.status_code == 401


def test_sin_cognito_sigue_siendo_noop(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "COGNITO_USER_POOL_ID", "")
    monkeypatch.setattr(config, "COGNITO_APP_CLIENT_ID", "")
    auth.require_auth(_request(None))  # dev/tests: no-op como siempre
