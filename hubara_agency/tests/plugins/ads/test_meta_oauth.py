"""OAuth de Meta (Facebook Login) — builder PURO del authorize URL.

`build_authorize_url` arma la URL del diálogo de OAuth de Meta (el destino del
botón "Conectar con Meta"). Es pura (sin IO) → testeable por inspección de la
query. El intercambio code→token (IO httpx) se testea aparte con respx.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from src.plugins.ads.meta.oauth import build_authorize_url


def test_authorize_url_targets_facebook_oauth_dialog() -> None:
    url = build_authorize_url(
        app_id="123", redirect_uri="https://app/cb", scopes=("ads_read",), state="st1"
    )
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.facebook.com"
    # versioned dialog path, p.ej. /v25.0/dialog/oauth
    assert "/dialog/oauth" in parsed.path
    assert parsed.path.startswith("/v")


def test_authorize_url_carries_required_oauth_params() -> None:
    url = build_authorize_url(
        app_id="123",
        redirect_uri="https://app/cb",
        scopes=("ads_read", "ads_management"),
        state="xyz",
    )
    q = parse_qs(urlparse(url).query)
    assert q["client_id"] == ["123"]
    assert q["redirect_uri"] == ["https://app/cb"]
    assert q["response_type"] == ["code"]
    assert q["state"] == ["xyz"]
    # scopes como lista separada por comas (formato del diálogo de FB)
    assert set(q["scope"][0].split(",")) == {"ads_read", "ads_management"}
