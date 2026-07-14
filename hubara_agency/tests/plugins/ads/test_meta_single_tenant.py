"""Single-tenant Meta (decisión 2026-07-09): el token de conexión se PROVISIONA
server-side (system-user token sembrado en SSM `/hubara/<tenant>/meta/oauth`),
no se obtiene por diálogo OAuth.

Consecuencias que estos tests fijan:
- NO existe superficie pública `/api/ads/meta/{login,callback}` (eran endpoints
  sin auth para un flujo que ya no existe — superficie de ataque gratuita).
- NO existe `/api/ads/meta/disconnect` (borraba el parámetro provisionado — un
  footgun sin flujo de re-conexión self-service).
- `/api/ads/meta/status` sigue PROTEGIDO y funcionando (la UI muestra el estado).
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.ads.api import meta_oauth
from src.plugins.ads.meta.token_store import InMemoryTokenStore, MetaToken


def test_loader_mounts_no_public_login_nor_callback(monkeypatch) -> None:
    """Vía el LOADER REAL con Cognito configurado: login/callback NO montados (404)
    y /status sigue exigiendo bearer (401) — la conexión es infra, no un flujo web."""
    from src.platform import config

    # Importar src.main ANTES de mutar el env: si este es el PRIMER import del
    # proceso, el `app` global del módulo se construye al importar — con
    # ENABLED_PLUGINS=ads ya seteado nacía solo-ads y envenenaba a todo test
    # posterior que use `from src.main import app` (fallo dependiente del
    # orden). `_bootstrap_routers` lee el env recién al LLAMARSE, así que el
    # comportamiento del test no cambia.
    from src.main import _bootstrap_routers

    monkeypatch.setattr(config, "COGNITO_USER_POOL_ID", "pool", raising=False)
    monkeypatch.setattr(config, "COGNITO_APP_CLIENT_ID", "client", raising=False)
    monkeypatch.setenv("ENABLED_PLUGINS", "ads")

    app = FastAPI()
    _bootstrap_routers(app)
    client = TestClient(app)

    assert client.get("/api/ads/meta/login", follow_redirects=False).status_code == 404
    assert client.get("/api/ads/meta/callback?state=x", follow_redirects=False).status_code == 404
    assert client.get("/api/ads/meta/status").status_code == 401


def test_disconnect_route_removed(monkeypatch) -> None:
    """El token provisionado no se borra desde la UI: /disconnect no existe."""
    store = InMemoryTokenStore()
    store.save(MetaToken("EAA", None, ("ads_read",), "act_1", "Hubara"))
    monkeypatch.setattr(meta_oauth, "_store", lambda: store)
    app = FastAPI()
    app.include_router(meta_oauth.router, prefix="/api/ads/meta")
    client = TestClient(app)

    assert client.post("/api/ads/meta/disconnect").status_code == 404
    assert store.load() is not None  # el token sigue ahí


def test_status_still_reports_provisioned_token(monkeypatch) -> None:
    """El status refleja el token provisionado (expires_at=None → nunca expirado)."""
    store = InMemoryTokenStore()
    store.save(
        MetaToken("EAA", None, ("ads_read", "ads_management"), "act_1010393601284112", "Hubara")
    )
    monkeypatch.setattr(meta_oauth, "_store", lambda: store)
    app = FastAPI()
    app.include_router(meta_oauth.router, prefix="/api/ads/meta")
    client = TestClient(app)

    body = client.get("/api/ads/meta/status").json()
    assert body["connected"] is True
    assert body["expired"] is False
    assert body["can_manage"] is True
