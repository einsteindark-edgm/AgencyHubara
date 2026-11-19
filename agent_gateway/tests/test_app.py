"""App HTTP del gateway: transporte Streamable HTTP + auth de resource server + límites.

Se ejercita por HTTP real (ASGI) con JSON-RPC como lo manda el cliente TS del plugin
(`@modelcontextprotocol/sdk` 1.32 → protocolo 2025-11-25, con `initialize`).
"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from hubara_gateway.app import GatewayLimits, create_app
from hubara_gateway.auth import CognitoTokenVerifier
from hubara_gateway.backend import BackendResponse
from tests.auth_helpers import ISSUER, PII, READ, RESOURCE, Issuer, StaticKeys, settings
from tests.test_tools import FakeBackend

PRM_URL = "http://localhost:8765/.well-known/oauth-protected-resource/mcp"
PROTOCOL = "2025-11-25"
CONV = {
    "id": "wa_573001234567__ep_001",
    "phone_number": "573001234567",
    "episode_id": "ep_001",
    "started_at_ms": 1760000000000,
    "last_msg_at_ms": None,
    "msgs_count": 4,
    "ad_headline": None,
    "agent": None,
    "state": "ganado",
    "value": None,
    "duration_ms": None,
    "name": "Cliente Fb764",
    "city": None,
    "llm_cost_usd": None,
    "llm_tokens": None,
}


@pytest.fixture
def issuer() -> Issuer:
    return Issuer()


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend(BackendResponse(200, {"campaign_id": "c1", "conversations": [CONV], "orders_stale": False}))


def _app(issuer: Issuer, backend: FakeBackend, **limits: Any) -> Any:
    cfg = settings()
    return create_app(
        cognito=cfg,
        verifier=CognitoTokenVerifier(cfg, key_resolver=StaticKeys(issuer)),
        backend=backend,
        allowed_origins=("http://localhost:*",),
        allowed_hosts=("localhost:*", "127.0.0.1:*"),
        limits=GatewayLimits(**limits),
    )


@asynccontextmanager
async def serving(app: Any, base_url: str = "http://localhost:8765") -> AsyncIterator[httpx.AsyncClient]:
    """Corre el lifespan (task group del SDK) en UNA task propia: anyio exige entrar y salir
    del cancel scope en la misma task, y el teardown de un fixture corre en otra."""
    started, stop = asyncio.Event(), asyncio.Event()

    async def run_lifespan() -> None:
        async with app.router.lifespan_context(app):
            started.set()
            await stop.wait()

    task = asyncio.create_task(run_lifespan())
    waiter = asyncio.create_task(started.wait())
    await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    if task.done():  # el lifespan murió antes de arrancar: propagar su error, no colgar
        waiter.cancel()
        task.result()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url) as c:
            yield c
    finally:
        stop.set()
        await task


@pytest.fixture
async def client(issuer: Issuer, backend: FakeBackend) -> AsyncIterator[httpx.AsyncClient]:
    async with serving(_app(issuer, backend)) as c:
        yield c


def _rpc(method: str, params: dict | None = None, id_: int | None = 1) -> dict:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if id_ is not None:
        msg["id"] = id_
    return msg


def _headers(token: str | None = None, **extra: str) -> dict[str, str]:
    h = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "mcp-protocol-version": PROTOCOL,
    }
    if token:
        h["authorization"] = f"Bearer {token}"
    return {**h, **extra}


async def _call(client: httpx.AsyncClient, token: str, name: str, arguments: dict) -> dict:
    r = await client.post(
        "/mcp", headers=_headers(token), json=_rpc("tools/call", {"name": name, "arguments": arguments})
    )
    assert r.status_code == 200, r.text
    return r.json()["result"]


# ── superficie pública ─────────────────────────────────────────────────────


async def test_healthz_is_public(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")

    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_protected_resource_metadata_points_to_cognito(client: httpx.AsyncClient) -> None:
    r = await client.get("/.well-known/oauth-protected-resource/mcp")

    assert r.status_code == 200
    prm = r.json()
    assert prm["resource"] == RESOURCE
    assert prm["authorization_servers"] == [ISSUER]
    assert set(prm["scopes_supported"]) == {READ, PII}
    assert prm["bearer_methods_supported"] == ["header"]


# ── challenge OAuth (RFC 6750 + MCP §Authorization) ───────────────────────


async def test_mcp_without_token_is_401_with_resource_metadata_and_scope(client: httpx.AsyncClient) -> None:
    r = await client.post("/mcp", headers=_headers(), json=_rpc("tools/list"))

    assert r.status_code == 401
    challenge = r.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert f'resource_metadata="{PRM_URL}"' in challenge
    assert f'scope="{READ}"' in challenge
    # RFC 6750 §3.1: sin credenciales, el challenge no lleva código de error.
    assert "error=" not in challenge


@pytest.mark.parametrize(
    "token_kind", ["garbage", "dashboard_client", "id_token", "other_audience", "service_token_shape"]
)
async def test_mcp_with_unacceptable_token_is_401_invalid_token(
    client: httpx.AsyncClient, issuer: Issuer, token_kind: str
) -> None:
    token = {
        "garbage": "abc.def.ghi",
        "dashboard_client": issuer.token(client_id="dashboardclient000000000"),
        "id_token": issuer.token(token_use="id"),
        "other_audience": issuer.token(aud="https://api.example.test"),
        "service_token_shape": "a" * 64,  # forma de HUBARA_SERVICE_TOKEN: el gateway no la conoce
    }[token_kind]

    r = await client.post("/mcp", headers=_headers(token), json=_rpc("tools/list"))

    assert r.status_code == 401
    challenge = r.headers["www-authenticate"]
    assert 'error="invalid_token"' in challenge
    assert f'resource_metadata="{PRM_URL}"' in challenge
    assert f'scope="{READ}"' in challenge


async def test_mcp_with_token_lacking_read_scope_is_403_insufficient_scope(
    client: httpx.AsyncClient, issuer: Issuer
) -> None:
    r = await client.post("/mcp", headers=_headers(issuer.token(scope="openid")), json=_rpc("tools/list"))

    assert r.status_code == 403
    challenge = r.headers["www-authenticate"]
    assert 'error="insufficient_scope"' in challenge
    assert f'scope="{READ}"' in challenge
    assert f'resource_metadata="{PRM_URL}"' in challenge


# ── transporte ─────────────────────────────────────────────────────────────


async def test_mcp_rejects_foreign_origin(client: httpx.AsyncClient, issuer: Issuer) -> None:
    r = await client.post(
        "/mcp", headers=_headers(issuer.token(), origin="https://evil.example.com"), json=_rpc("tools/list")
    )

    assert r.status_code == 403


async def test_mcp_rejects_foreign_host_header(issuer: Issuer, backend: FakeBackend) -> None:
    async with serving(_app(issuer, backend), base_url="http://evil.example.com") as c:
        r = await c.post("/mcp", headers=_headers(issuer.token()), json=_rpc("tools/list"))

    assert r.status_code in (403, 421)


async def test_mcp_rejects_oversized_body(issuer: Issuer, backend: FakeBackend) -> None:
    async with serving(_app(issuer, backend, max_body_bytes=1024)) as c:
        big = _rpc("tools/call", {"name": "ads_get_campaigns", "arguments": {"window": "x" * 4096}})
        r = await c.post("/mcp", headers=_headers(issuer.token()), json=big)

    assert r.status_code == 413


async def test_mcp_rate_limits_per_principal(issuer: Issuer, backend: FakeBackend) -> None:
    async with serving(_app(issuer, backend, rate_limit_per_minute=3)) as c:
        token = issuer.token()
        codes = [(await c.post("/mcp", headers=_headers(token), json=_rpc("tools/list"))).status_code for _ in range(4)]
        other = issuer.token(sub="user-sub-2")
        other_code = (await c.post("/mcp", headers=_headers(other), json=_rpc("tools/list"))).status_code

    assert codes == [200, 200, 200, 429]
    assert other_code == 200


# ── protocolo MCP de punta a punta ─────────────────────────────────────────


async def test_initialize_with_2025_11_25_client_is_stateless(client: httpx.AsyncClient, issuer: Issuer) -> None:
    init = _rpc(
        "initialize",
        {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "@acktos/dsh-hubara", "version": "0.1.0"}},
    )

    r = await client.post("/mcp", headers=_headers(issuer.token()), json=init)

    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["protocolVersion"] == PROTOCOL
    assert "tools" in result["capabilities"]
    assert "mcp-session-id" not in {k.lower() for k in r.headers}


async def test_tools_list_exposes_read_only_tools_with_schemas(client: httpx.AsyncClient, issuer: Issuer) -> None:
    r = await client.post("/mcp", headers=_headers(issuer.token()), json=_rpc("tools/list"))

    tools = {t["name"]: t for t in r.json()["result"]["tools"]}
    assert len(tools) == 11
    conv = tools["ads_get_conversations"]
    assert conv["annotations"]["readOnlyHint"] is True
    assert conv["annotations"]["destructiveHint"] is False
    assert conv["outputSchema"]["type"] == "object"
    assert conv["inputSchema"]["required"] == ["campaign_id"]
    assert set(conv["inputSchema"]["properties"]) == {"campaign_id", "adset_id", "window"}
    assert all(p["type"] == "string" for p in conv["inputSchema"]["properties"].values())
    assert tools["ads_get_meta_status"]["inputSchema"]["properties"] == {}


async def test_tools_call_masks_pii_and_audits_subject(
    client: httpx.AsyncClient, issuer: Issuer, backend: FakeBackend
) -> None:
    result = await _call(client, issuer.token(), "ads_get_conversations", {"campaign_id": "c1", "window": "7d"})

    row = result["structuredContent"]["conversations"][0]
    assert row["phone_number"] == "•••••••4567"
    assert "573001234567" not in json.dumps(result)
    assert backend.calls == [("/api/ads/campaigns/c1/conversations", {"days": "7"}, "user-sub-1")]


async def test_tools_call_unmasks_pii_for_admin_with_pii_scope(client: httpx.AsyncClient, issuer: Issuer) -> None:
    token = issuer.token(scope=f"{READ} {PII}", **{"cognito:groups": ["admin"]})

    result = await _call(client, token, "ads_get_conversations", {"campaign_id": "c1"})

    assert result["structuredContent"]["conversations"][0]["phone_number"] == "573001234567"


async def test_tools_call_404_reaches_client_as_is_error(client: httpx.AsyncClient, issuer: Issuer, backend: FakeBackend) -> None:
    backend.response = BackendResponse(404, {"detail": "run no existe"})

    result = await _call(client, issuer.token(), "ads_get_analysis_run", {"run_id": "run-000000000000"})

    assert result["isError"] is True
    assert "404" in result["content"][0]["text"]


async def test_any_other_path_requires_auth_by_default(client: httpx.AsyncClient) -> None:
    """M3: deny-by-default; solo /healthz y el PRM son públicos."""
    for path in ("/mcp/", "/debug", "/.well-known/other"):
        r = await client.get(path)
        assert r.status_code == 401, path
