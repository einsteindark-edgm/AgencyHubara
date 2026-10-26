"""Configuración SOLO por variables de entorno; sin secretos por defecto."""

import pytest

from hubara_gateway.config import ConfigError, GatewayConfig

BASE = {
    "GATEWAY_RESOURCE_URL": "http://localhost:8765/mcp",
    "COGNITO_USER_POOL_ID": "us-east-1_EXAMPLE",
    "GATEWAY_ALLOWED_CLIENT_IDS": "client-a, client-b",
    "BACKEND_URL": "https://api.example.test",
    "COGNITO_DOMAIN": "agencyhubara-hubara.auth.us-east-1.amazoncognito.com",
    "BACKEND_M2M_CLIENT_ID": "gw-client",
    "BACKEND_M2M_CLIENT_SECRET": "gw-secret",
}


def test_config_from_env_with_defaults() -> None:
    cfg = GatewayConfig.from_env(BASE)

    assert cfg.cognito.issuer == "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_EXAMPLE"
    assert cfg.cognito.allowed_client_ids == ("client-a", "client-b")
    assert cfg.cognito.read_scope == "hubara-mcp/ads.read"
    assert cfg.cognito.pii_scope == "hubara-mcp/ads.pii"
    assert cfg.cognito.pii_group == "admin"
    assert cfg.cognito.require_audience is True
    assert cfg.m2m_token_url == "https://agencyhubara-hubara.auth.us-east-1.amazoncognito.com/oauth2/token"
    assert cfg.m2m_scope == "hubara-api/ads.read"
    assert cfg.allowed_hosts == ("localhost:8765",)
    assert cfg.allowed_origins == ()
    assert cfg.port == 8765


@pytest.mark.parametrize("missing", sorted(BASE))
def test_config_missing_required_var_fails_clearly(missing: str) -> None:
    env = {k: v for k, v in BASE.items() if k != missing}

    with pytest.raises(ConfigError, match=missing):
        GatewayConfig.from_env(env)


def test_config_refuses_the_god_service_token() -> None:
    with pytest.raises(ConfigError, match="HUBARA_SERVICE_TOKEN"):
        GatewayConfig.from_env({**BASE, "HUBARA_SERVICE_TOKEN": "x"})


def test_config_resource_url_must_be_https_or_localhost() -> None:
    with pytest.raises(ConfigError, match="GATEWAY_RESOURCE_URL"):
        GatewayConfig.from_env({**BASE, "GATEWAY_RESOURCE_URL": "http://gateway.example.com/mcp"})


def test_config_parses_lists_and_overrides() -> None:
    cfg = GatewayConfig.from_env(
        {
            **BASE,
            "GATEWAY_RESOURCE_URL": "https://api.example.test/mcp",
            "GATEWAY_ALLOWED_HOSTS": "api.example.test, gateway:8765",
            "GATEWAY_ALLOWED_ORIGINS": "https://app.example.com",
            "GATEWAY_RATE_LIMIT_PER_MINUTE": "30",
            "GATEWAY_PORT": "9000",
        }
    )

    assert cfg.allowed_hosts == ("api.example.test", "gateway:8765")
    assert cfg.allowed_origins == ("https://app.example.com",)
    assert cfg.limits.rate_limit_per_minute == 30
    assert cfg.port == 9000


def test_config_repr_never_shows_the_m2m_secret() -> None:
    assert "gw-secret" not in repr(GatewayConfig.from_env(BASE))


def test_config_audience_can_be_relaxed_only_for_localhost() -> None:
    """M4: sin `aud` el token no está atado al gateway; solo se tolera en desarrollo local."""
    local = GatewayConfig.from_env({**BASE, "GATEWAY_REQUIRE_AUDIENCE": "false"})
    assert local.cognito.require_audience is False

    with pytest.raises(ConfigError, match="GATEWAY_REQUIRE_AUDIENCE"):
        GatewayConfig.from_env(
            {**BASE, "GATEWAY_RESOURCE_URL": "https://api.example.test/mcp", "GATEWAY_REQUIRE_AUDIENCE": "false"}
        )
