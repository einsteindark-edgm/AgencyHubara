"""Sub-router PÚBLICO `/api/ads/meta/{login,callback}` — sin `require_auth`.

Por qué público: el botón "Conectar con Meta" es una navegación top-level del
browser (no lleva bearer de Cognito), y el **callback lo invoca Meta** redirigiendo
el browser (tampoco lleva bearer). Si heredaran `require_auth`, con Cognito
configurado en prod ambos darían 401 y el OAuth nunca completaría (premortem #2).

La seguridad del callback NO es Cognito: es el `state` HMAC stateless (anti-CSRF +
anti-replay) firmado con el App Secret en `/login` y verificado acá. El resto de
`/api/ads/meta/*` (status/insights/disconnect/gestión) vive en `meta_oauth.py` y
SIGUE protegido. Este módulo se registra aparte vía `legacy_routers` (plugin.yaml)
con `PUBLIC_ROUTER = True`.

El token vive server-side (SSM SecureString) y NUNCA se loguea (ni el `code`).
"""
from __future__ import annotations

import time

import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from src.plugins.ads.meta.client import MetaAdsPort
from src.plugins.ads.meta.composition import get_ads_port, get_token_store
from src.plugins.ads.meta.oauth import (
    build_authorize_url,
    exchange_code,
    exchange_for_long_lived,
    make_state,
    verify_state,
)
from src.plugins.ads.meta.settings import MetaSettings, meta_settings
from src.plugins.ads.meta.token_store import MetaToken, MetaTokenStorePort

#: Marca el router como PÚBLICO para el loader (`src/main.py` no le cuelga require_auth).
PUBLIC_ROUTER = True

logger = structlog.get_logger()
router = APIRouter()

_RETURN_OK = "/ads?meta=connected"
_RETURN_ERR = "/ads?meta=error"


def _settings() -> MetaSettings:
    return meta_settings()


def _store() -> MetaTokenStorePort:
    return get_token_store()


def _ads() -> MetaAdsPort:
    return get_ads_port()


@router.get("/login")
def meta_login() -> RedirectResponse:
    settings = _settings()
    if not settings.configured:
        raise HTTPException(status_code=503, detail="Meta OAuth no configurado (faltan env vars)")
    state = make_state(settings.app_secret)
    url = build_authorize_url(
        app_id=settings.app_id,
        redirect_uri=settings.redirect_uri,
        scopes=settings.scopes,
        state=state,
    )
    return RedirectResponse(url)


@router.get("/callback")
def meta_callback(code: str = "", state: str = "", error: str = "") -> RedirectResponse:
    import httpx

    settings = _settings()
    if error:
        return RedirectResponse(_RETURN_ERR)
    if not verify_state(settings.app_secret, state):
        raise HTTPException(status_code=400, detail="state inválido")
    if not code:
        raise HTTPException(status_code=400, detail="falta el code")

    # Cualquier fallo de Graph (4xx/5xx, timeout, rate-limit BUC, cuenta no
    # consultable, respuesta malformada) degrada a `?meta=error` en vez de un 500
    # crudo (premortem #3). NO se loguea el code ni el token.
    try:
        short = exchange_code(
            app_id=settings.app_id,
            app_secret=settings.app_secret,
            redirect_uri=settings.redirect_uri,
            code=code,
        )
        longed = exchange_for_long_lived(
            app_id=settings.app_id,
            app_secret=settings.app_secret,
            short_token=short["access_token"],
        )
        access = longed.get("access_token") or short["access_token"]
        expires_in = int(longed.get("expires_in") or short.get("expires_in") or 0)
        accounts = _ads().list_ad_accounts(access)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.warning("meta_oauth.callback.exchange_failed", error=type(exc).__name__)
        return RedirectResponse(_RETURN_ERR)

    expires_at = int(time.time()) + expires_in if expires_in else None
    account_id = account_name = None
    if accounts:
        account_id, account_name = accounts[0].account_id, accounts[0].name

    _store().save(
        MetaToken(
            access_token=access,
            expires_at=expires_at,
            scopes=settings.scopes,
            account_id=account_id,
            account_name=account_name,
        )
    )
    return RedirectResponse(_RETURN_OK)
