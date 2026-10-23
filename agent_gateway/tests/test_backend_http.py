"""Cliente HTTP hacia `/api/ads/*`: solo GET, con el token M2M PROPIO del gateway.

Sin token passthrough (spec MCP, Security Best Practices): el access token del usuario
jamás sale del gateway; al backend va un token `client_credentials` de Cognito con scope
`hubara-api/ads.read`, y la identidad del usuario solo como header de auditoría.
"""

import base64
import json
import time
from urllib.parse import parse_qs

import httpx
import pytest

from hubara_gateway.backend import BackendUnavailable, HttpBackend, M2MTokenProvider

TOKEN_URL = "https://agencyhubara-hubara.auth.us-east-1.amazoncognito.com/oauth2/token"
API = "https://api.example.test"


class Cognito:
    """Token endpoint falso: emite tokens numerados y registra cada pedido."""

    def __init__(self, expires_in: int = 3600) -> None:
        self.requests: list[httpx.Request] = []
        self.expires_in = expires_in

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(
            200, json={"access_token": f"m2m-{len(self.requests)}", "expires_in": self.expires_in, "token_type": "Bearer"}
        )


def _provider(cognito: Cognito, clock=time.monotonic) -> M2MTokenProvider:
    return M2MTokenProvider(
        token_url=TOKEN_URL,
        client_id="gateway-client",
        client_secret="s3cret",
        scope="hubara-api/ads.read",
        http=httpx.AsyncClient(transport=httpx.MockTransport(cognito)),
        clock=clock,
    )


async def test_m2m_token_uses_client_credentials_with_basic_auth_and_scope() -> None:
    cognito = Cognito()

    token = await _provider(cognito).token()

    assert token == "m2m-1"
    req = cognito.requests[0]
    assert req.method == "POST"
    assert str(req.url) == TOKEN_URL
    assert req.headers["authorization"] == "Basic " + base64.b64encode(b"gateway-client:s3cret").decode()
    form = parse_qs(req.content.decode())
    assert form == {"grant_type": ["client_credentials"], "scope": ["hubara-api/ads.read"]}


async def test_m2m_token_is_cached_until_shortly_before_expiry() -> None:
    cognito, now = Cognito(expires_in=3600), [1000.0]
    provider = _provider(cognito, clock=lambda: now[0])

    assert await provider.token() == "m2m-1"
    now[0] += 3500  # aún > 60 s de margen
    assert await provider.token() == "m2m-1"
    now[0] += 50  # dentro del margen de 60 s → renueva
    assert await provider.token() == "m2m-2"
    assert len(cognito.requests) == 2


async def test_m2m_token_endpoint_failure_is_backend_unavailable() -> None:
    provider = M2MTokenProvider(
        token_url=TOKEN_URL,
        client_id="c",
        client_secret="s",
        scope="hubara-api/ads.read",
        http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(400, json={"error": "invalid_client"}))),
    )

    with pytest.raises(BackendUnavailable, match="m2m_token"):
        await provider.token()


def _backend(handler, cognito: Cognito | None = None, **kw) -> tuple[HttpBackend, Cognito]:
    cognito = cognito or Cognito()
    backend = HttpBackend(
        base_url=API,
        tokens=_provider(cognito),
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        **kw,
    )
    return backend, cognito


async def test_get_sends_only_get_with_m2m_bearer_and_audit_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"campaigns": [], "orders_stale": False})

    backend, _ = _backend(handler)
    response = await backend.get("/api/ads/campaigns", {"days": "7"}, on_behalf_of="user-sub-1")

    assert response.status == 200
    assert response.body == {"campaigns": [], "orders_stale": False}
    req = seen[0]
    assert req.method == "GET"
    assert str(req.url) == f"{API}/api/ads/campaigns?days=7"
    assert req.headers["authorization"] == "Bearer m2m-1"
    assert req.headers["x-hubara-on-behalf-of"] == "user-sub-1"
    assert req.content == b""


async def test_get_refuses_paths_outside_ads_api() -> None:
    backend, _ = _backend(lambda r: httpx.Response(200, json={}))

    with pytest.raises(ValueError):
        await backend.get("/api/chats/sessions", {})
    with pytest.raises(ValueError):
        await backend.get("https://evil.example.com/api/ads/campaigns", {})


async def test_get_returns_error_status_and_json_body() -> None:
    backend, _ = _backend(lambda r: httpx.Response(404, json={"detail": "run no existe"}))

    response = await backend.get("/api/ads/analysis/runs/run-000000000000", {})

    assert (response.status, response.body) == (404, {"detail": "run no existe"})


async def test_get_retries_once_with_fresh_token_on_401() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["authorization"])
        return httpx.Response(401 if len(calls) == 1 else 200, json={"connected": False})

    backend, cognito = _backend(handler)
    response = await backend.get("/api/ads/meta/status", {})

    assert response.status == 200
    assert calls == ["Bearer m2m-1", "Bearer m2m-2"]
    assert len(cognito.requests) == 2


async def test_get_timeout_is_backend_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    backend, _ = _backend(handler)

    with pytest.raises(BackendUnavailable, match="timeout"):
        await backend.get("/api/ads/campaigns", {})


async def test_get_oversized_response_is_backend_unavailable() -> None:
    big = json.dumps({"campaigns": ["x" * 2048]}).encode()
    backend, _ = _backend(lambda r: httpx.Response(200, content=big), max_response_bytes=1024)

    with pytest.raises(BackendUnavailable, match="too large"):
        await backend.get("/api/ads/campaigns", {})


async def test_get_non_json_body_is_returned_as_none() -> None:
    backend, _ = _backend(lambda r: httpx.Response(502, text="<html>Bad gateway</html>"))

    response = await backend.get("/api/ads/campaigns", {})

    assert (response.status, response.body) == (502, None)
