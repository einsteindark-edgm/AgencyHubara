"""OAuth de Meta — intercambio code→token (IO httpx, mockeado con respx)."""
from __future__ import annotations

import httpx
import respx

from src.plugins.ads.meta.oauth import exchange_code, exchange_for_long_lived

_TOKEN_URL = "https://graph.facebook.com/v25.0/oauth/access_token"


@respx.mock
def test_exchange_code_returns_access_token() -> None:
    route = respx.get(_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "EAA-short", "expires_in": 3600})
    )
    res = exchange_code(app_id="123", app_secret="sec", redirect_uri="https://app/cb", code="c0de")
    assert res["access_token"] == "EAA-short"
    # los 4 params OAuth viajan en la query
    sent = dict(route.calls.last.request.url.params)
    assert sent["client_id"] == "123"
    assert sent["redirect_uri"] == "https://app/cb"
    assert sent["client_secret"] == "sec"
    assert sent["code"] == "c0de"


@respx.mock
def test_exchange_for_long_lived_uses_fb_exchange_token_grant() -> None:
    route = respx.get(_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "EAA-long", "expires_in": 5184000})
    )
    res = exchange_for_long_lived(app_id="123", app_secret="sec", short_token="EAA-short")
    assert res["access_token"] == "EAA-long"
    sent = dict(route.calls.last.request.url.params)
    assert sent["grant_type"] == "fb_exchange_token"
    assert sent["fb_exchange_token"] == "EAA-short"


@respx.mock
def test_exchange_code_raises_on_oauth_error() -> None:
    respx.get(_TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": {"message": "bad code"}})
    )
    try:
        exchange_code(app_id="1", app_secret="s", redirect_uri="r", code="bad")
    except httpx.HTTPStatusError:
        return
    raise AssertionError("debió levantar HTTPStatusError en 400")
