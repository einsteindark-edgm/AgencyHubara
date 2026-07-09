"""OAuth de Meta (Facebook Login) — authorize URL + intercambio code→token.

Decisión de arquitectura: Marketing API estándar vía Meta App propia. El
`build_authorize_url` es PURO; el intercambio code→token hace IO httpx (lazy).
"""
from __future__ import annotations

from urllib.parse import urlencode

GRAPH_VERSION = "v25.0"
GRAPH_BASE = "https://graph.facebook.com"
_TIMEOUT_S = 20.0


def build_authorize_url(
    *, app_id: str, redirect_uri: str, scopes: tuple[str, ...], state: str
) -> str:
    """Arma la URL del diálogo de OAuth de Meta (Facebook Login, authorization code).

    `state` es el anti-CSRF que el callback debe verificar. Los scopes van en lista
    separada por comas (formato del diálogo de FB).
    """
    query = urlencode(
        {
            "client_id": app_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
            "scope": ",".join(scopes),
        }
    )
    return f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth?{query}"


def _token_endpoint(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/{GRAPH_VERSION}/oauth/access_token"


def _sign(secret: str, message: str) -> str:
    import hashlib
    import hmac

    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()[:32]


def make_state(secret: str, *, nonce: str = "n", now: float | None = None) -> str:
    """CSRF `state` STATELESS firmado con HMAC(app_secret) — el callback lo verifica sin
    estado server-side. Formato `<ts>.<nonce>.<sig>`."""
    import time

    ts = str(int(now if now is not None else time.time()))
    return f"{ts}.{nonce}.{_sign(secret, f'{ts}.{nonce}')}"


def verify_state(
    secret: str, state: str, *, max_age_s: int = 600, now: float | None = None
) -> bool:
    """True si la firma del `state` matchea y no expiró (anti-CSRF + anti-replay)."""
    import hmac
    import time

    parts = state.split(".")
    if len(parts) != 3:
        return False
    ts_s, nonce, sig = parts
    if not hmac.compare_digest(sig, _sign(secret, f"{ts_s}.{nonce}")):
        return False
    try:
        age = (now if now is not None else time.time()) - int(ts_s)
    except ValueError:
        return False
    return -5 <= age <= max_age_s


def exchange_code(
    *, app_id: str, app_secret: str, redirect_uri: str, code: str, base_url: str = GRAPH_BASE
) -> dict:
    """Intercambia el authorization `code` por un access token (Graph token endpoint).

    Devuelve el JSON crudo (`access_token`, `expires_in`). Levanta `HTTPStatusError`
    si Meta responde error (el caller lo traduce a 400/redirect).
    """
    import httpx

    params = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "client_secret": app_secret,
        "code": code,
    }
    with httpx.Client(timeout=_TIMEOUT_S) as client:
        resp = client.get(_token_endpoint(base_url), params=params)
    resp.raise_for_status()
    return resp.json()


def exchange_for_long_lived(
    *, app_id: str, app_secret: str, short_token: str, base_url: str = GRAPH_BASE
) -> dict:
    """Cambia un token corto por uno long-lived (~60d) vía `grant_type=fb_exchange_token`."""
    import httpx

    params = {
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": short_token,
    }
    with httpx.Client(timeout=_TIMEOUT_S) as client:
        resp = client.get(_token_endpoint(base_url), params=params)
    resp.raise_for_status()
    return resp.json()
