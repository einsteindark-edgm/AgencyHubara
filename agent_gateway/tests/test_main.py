"""El ensamblado desde el entorno arranca sin red (JWKS y token M2M son perezosos)."""

import httpx

from hubara_gateway.__main__ import build_app
from hubara_gateway.config import GatewayConfig
from tests.test_app import serving
from tests.test_config import BASE


async def test_build_app_from_env_serves_health_and_prm_without_network() -> None:
    app = build_app(GatewayConfig.from_env(BASE))

    async with serving(app) as client:
        health = await client.get("/healthz")
        prm = await client.get("/.well-known/oauth-protected-resource/mcp")
        denied = await client.post("/mcp", json={})

    assert health.status_code == 200
    assert prm.json()["authorization_servers"] == ["https://cognito-idp.us-east-1.amazonaws.com/us-east-1_EXAMPLE"]
    assert denied.status_code == 401
    assert isinstance(client, httpx.AsyncClient)
