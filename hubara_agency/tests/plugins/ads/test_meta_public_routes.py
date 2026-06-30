"""Rutas PÚBLICAS `/api/ads/meta/{login,callback}` (meta_public, PUBLIC_ROUTER).

Login/callback los invoca el browser/Meta SIN bearer de Cognito → deben ser
públicas. La seguridad del callback es el `state` HMAC, no Cognito.
"""
from __future__ import annotations

import httpx
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.ads.api import meta_public
from src.plugins.ads.meta.client import FakeMetaAds, MetaAdAccount
from src.plugins.ads.meta.oauth import make_state
from src.plugins.ads.meta.settings import MetaSettings
from src.plugins.ads.meta.token_store import InMemoryTokenStore

_SETTINGS = MetaSettings(
    app_id="123",
    app_secret="sec",
    redirect_uri="https://app/api/ads/meta/callback",
    scopes=("ads_read",),
    tenant="hubara",
    region=None,
)


def _client(monkeypatch, *, store=None, ads=None, settings=_SETTINGS) -> TestClient:
    store = store or InMemoryTokenStore()
    ads = ads or FakeMetaAds(accounts=[MetaAdAccount("act_1010393601284112", "Hubara", "COP", 1)])
    monkeypatch.setattr(meta_public, "_settings", lambda: settings)
    monkeypatch.setattr(meta_public, "_store", lambda: store)
    monkeypatch.setattr(meta_public, "_ads", lambda: ads)
    app = FastAPI()
    app.include_router(meta_public.router, prefix="/api/ads/meta")
    return TestClient(app)


def test_public_router_flag_is_set() -> None:
    # El loader (main.py) lee PUBLIC_ROUTER para NO colgar require_auth.
    assert meta_public.PUBLIC_ROUTER is True


def test_loader_mounts_login_public_but_status_protected(monkeypatch) -> None:
    """Premortem #2: vía el LOADER REAL, con Cognito configurado, /login y /callback
    deben ser alcanzables SIN bearer (públicos) mientras /status exige auth (401)."""
    from src.platform import config

    monkeypatch.setattr(config, "COGNITO_USER_POOL_ID", "pool", raising=False)
    monkeypatch.setattr(config, "COGNITO_APP_CLIENT_ID", "client", raising=False)
    monkeypatch.setenv("ENABLED_PLUGINS", "ads")

    from src.main import _bootstrap_routers

    app = FastAPI()
    _bootstrap_routers(app)
    client = TestClient(app)

    # Público: sin bearer NO da 401 (503 meta-no-configurado o 307, pero nunca 401).
    assert client.get("/api/ads/meta/login", follow_redirects=False).status_code != 401
    assert client.get("/api/ads/meta/callback?state=x", follow_redirects=False).status_code != 401
    # Protegido: sin bearer → 401.
    assert client.get("/api/ads/meta/status").status_code == 401


def test_login_redirects_to_facebook_dialog(monkeypatch) -> None:
    client = _client(monkeypatch)
    resp = client.get("/api/ads/meta/login", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"].startswith("https://www.facebook.com/")
    assert "client_id=123" in resp.headers["location"]


def test_login_503_when_unconfigured(monkeypatch) -> None:
    unconf = MetaSettings("", "", "", ("ads_read",), "hubara", None)
    client = _client(monkeypatch, settings=unconf)
    assert client.get("/api/ads/meta/login").status_code == 503


@respx.mock
def test_callback_exchanges_code_and_persists_token(monkeypatch) -> None:
    respx.get("https://graph.facebook.com/v25.0/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "EAA-long", "expires_in": 5184000})
    )
    store = InMemoryTokenStore()
    client = _client(monkeypatch, store=store)
    resp = client.get(
        f"/api/ads/meta/callback?code=the-code&state={make_state('sec')}",
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert "meta=connected" in resp.headers["location"]
    saved = store.load()
    assert saved is not None
    assert saved.access_token == "EAA-long"
    assert saved.account_id == "act_1010393601284112"


def test_callback_rejects_invalid_state(monkeypatch) -> None:
    client = _client(monkeypatch)
    resp = client.get("/api/ads/meta/callback?code=c&state=forged.n.deadbeef")
    assert resp.status_code == 400


@respx.mock
def test_callback_redirects_to_error_on_graph_failure(monkeypatch) -> None:
    # Graph 400 en el intercambio → degradar a /ads?meta=error, NO un 500 crudo (premortem #3).
    respx.get("https://graph.facebook.com/v25.0/oauth/access_token").mock(
        return_value=httpx.Response(400, json={"error": {"message": "bad code"}})
    )
    store = InMemoryTokenStore()
    client = _client(monkeypatch, store=store)
    resp = client.get(
        f"/api/ads/meta/callback?code=bad&state={make_state('sec')}", follow_redirects=False
    )
    assert resp.status_code in (302, 307)
    assert "meta=error" in resp.headers["location"]
    assert store.load() is None  # no persistió token roto
