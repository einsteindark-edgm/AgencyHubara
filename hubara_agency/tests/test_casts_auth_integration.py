"""Guard de integración del incidente 2026-06-23 — casts loopback + auth ON.

Reproduce el bug REAL end-to-end (no a nivel schema): con ``require_auth``
enforced (JWT/Cognito), una llamada al cast ``agents_admin→chats``
(``GET /api/agents/evals/history``) DEBE propagar el Bearer al 2º hop
(``/api/chats/evals/history``) para que el provider pase su propio
``require_auth``. Antes del fix, el cast no reenviaba el header → el 2º hop
devolvía ``401 {"detail":"Falta el bearer token"}`` y el cast lo re-envolvía →
``detail`` doble-anidado.

El test es ROBUSTO a la implementación: parchea ``httpx.AsyncClient`` para que el
self-call loopback REENTRE a la MISMA app de test vía ``ASGITransport`` (no a un
``:8000`` real), así el 2º hop atraviesa ``require_auth`` de verdad. No depende
de en qué módulo viva el ``httpx`` (gotcha #1: verificar comportamiento, no
schema).
"""
from __future__ import annotations

import httpx
import pytest

_VALID_CLAIMS = {"sub": "u1", "client_id": "client", "token_use": "access"}


@pytest.fixture
def auth_app(monkeypatch):
    """App real con Cognito enforced y ``_verify_token`` mockeado (sin red).

    El mismo token vale en AMBOS hops (es la misma app/proceso → el mismo
    ``_verify_token``), igual que en prod el Bearer del operador vale en el edge
    y en el provider (mismo pool/client).
    """
    from src.main import app
    from src.platform import config

    monkeypatch.setattr(config, "COGNITO_USER_POOL_ID", "pool")
    monkeypatch.setattr(config, "COGNITO_APP_CLIENT_ID", "client")
    monkeypatch.setattr(config, "AWS_REGION", "us-east-1")
    monkeypatch.setattr("src.platform.auth._verify_token", lambda _t: _VALID_CLAIMS)
    return app


def _route_loopback_to(app, monkeypatch) -> None:
    """Hace que ``httpx.AsyncClient(...)`` del cast reentre a ``app`` (ASGI).

    Guardamos el ``AsyncClient`` real ANTES de parchear para (a) construir el
    client del edge sin auto-referencia y (b) que el loopback use el real con un
    ``ASGITransport`` apuntando a la app.
    """
    real = httpx.AsyncClient

    def _loopback(**kwargs):
        kwargs.pop("transport", None)
        return real(transport=httpx.ASGITransport(app=app), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _loopback)
    return real


async def test_evals_cast_propagates_bearer_through_loopback(auth_app, monkeypatch):
    """El cast agents_admin→chats porta el Bearer → el 2º hop NO da 401."""
    real = _route_loopback_to(auth_app, monkeypatch)

    async with real(
        transport=httpx.ASGITransport(app=auth_app), base_url="http://edge"
    ) as edge:
        resp = await edge.get(
            "/api/agents/evals/history",
            headers={"Authorization": "Bearer good"},
        )

    # Pre-fix: 401 con detail doble-anidado. Post-fix: el 2º hop pasa la auth.
    assert resp.status_code != 401, resp.text
    assert "Falta el bearer token" not in resp.text


async def test_evals_cast_still_401_when_edge_has_no_token(auth_app, monkeypatch):
    """Control: sin Bearer en el edge, el cast NO debe colarse (fail-closed)."""
    _route_loopback_to(auth_app, monkeypatch)
    from fastapi.testclient import TestClient

    client = TestClient(auth_app)
    resp = client.get("/api/agents/evals/history")
    assert resp.status_code == 401


# --- D1.2b: cast mba→chats con service token (el edge de Meta no trae Bearer) ---


async def test_mba_write_tool_reaches_chats_with_the_service_token(auth_app, monkeypatch, tmp_path):
    """Meta llama al connector con ``X-API-Key``; el cast a ``session-actions@v1``
    debe pasar ``require_auth`` de chats con ``HUBARA_SERVICE_TOKEN`` y escribir
    el vault de la sesión del teléfono (comportamiento real, no schema)."""
    from src.platform import config
    from src.plugins.chats.api import session_actions

    async def _noop_flush(_s: str) -> int:
        return 0

    async def _noop_notify(*_a) -> None:
        return None

    monkeypatch.setattr(config, "HUBARA_SERVICE_TOKEN", "svc-token-1")
    monkeypatch.setenv("HUBARA_MBA_API_KEY", "meta-key")
    deps = session_actions.SessionActionsDeps(
        vault_dir=tmp_path, catalog=None, order_port=None, flush=_noop_flush, notify_episode_closed=_noop_notify
    )
    auth_app.dependency_overrides[session_actions.get_session_actions_deps] = lambda: deps
    real = _route_loopback_to(auth_app, monkeypatch)
    try:
        async with real(transport=httpx.ASGITransport(app=auth_app), base_url="http://edge") as edge:
            resp = await edge.post(
                "/api/mba/tools/set_order_slot",
                headers={"X-API-Key": "meta-key"},
                json={"customer_phone": "+573001234567", "producto": "luz-serena"},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["updated"] is True
            assert (tmp_path / "wa_573001234567" / "metadata.json").exists()

            # Control: sin token de servicio real, el 2º hop es 401 y el connector
            # lo reporta como rechazo explícito (nunca un 500 hacia Meta).
            monkeypatch.setattr(config, "HUBARA_SERVICE_TOKEN", "")
            resp = await edge.post(
                "/api/mba/tools/set_order_slot",
                headers={"X-API-Key": "meta-key"},
                json={"customer_phone": "+573001234567", "aroma": "Lavanda"},
            )
            assert resp.status_code == 200 and resp.json() == {
                **resp.json(), "error": "rejected", "status": 401, "applied": False,
            }
    finally:
        auth_app.dependency_overrides.pop(session_actions.get_session_actions_deps, None)
