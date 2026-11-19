"""Configuración del gateway: SOLO variables de entorno, sin secretos por defecto.

Ver `agent_gateway/README.md` (tabla de variables) y `.env.example`.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from hubara_gateway.app import GatewayLimits
from hubara_gateway.auth import CognitoSettings


class ConfigError(ValueError):
    pass


def _list(raw: str | None) -> tuple[str, ...]:
    return tuple(item.strip() for item in (raw or "").split(",") if item.strip())


def _bool(raw: str | None, default: bool) -> bool:
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class GatewayConfig:
    cognito: CognitoSettings
    backend_url: str
    m2m_token_url: str
    m2m_client_id: str
    m2m_client_secret: str = field(repr=False)
    m2m_scope: str = "hubara-api/ads.read"
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()
    limits: GatewayLimits = GatewayLimits()
    backend_timeout_s: float = 20.0
    backend_max_response_bytes: int = 8 * 1024 * 1024
    bind: str = "0.0.0.0"
    port: int = 8765

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "GatewayConfig":
        if env.get("HUBARA_SERVICE_TOKEN"):
            raise ConfigError(
                "HUBARA_SERVICE_TOKEN is set: the gateway never uses the service token "
                "(it calls the backend with its own Cognito M2M client). Remove it from this environment."
            )

        def need(name: str) -> str:
            value = env.get(name, "").strip()
            if not value:
                raise ConfigError(f"missing required environment variable {name}")
            return value

        resource_url = need("GATEWAY_RESOURCE_URL")
        parts = urlsplit(resource_url)
        if not (parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1"})):
            # Cognito (resource binding) y OAuth 2.1 solo aceptan https o http://localhost.
            raise ConfigError("GATEWAY_RESOURCE_URL must be https:// or http://localhost")

        require_audience = _bool(env.get("GATEWAY_REQUIRE_AUDIENCE"), True)
        if not require_audience and parts.hostname not in {"localhost", "127.0.0.1"}:
            # Sin `aud` el token no queda atado a ESTE gateway (spec MCP: MUST validar audiencia).
            raise ConfigError("GATEWAY_REQUIRE_AUDIENCE=false is only allowed for a localhost GATEWAY_RESOURCE_URL")

        client_ids = _list(need("GATEWAY_ALLOWED_CLIENT_IDS"))
        cognito = CognitoSettings(
            region=env.get("COGNITO_REGION", "us-east-1"),
            user_pool_id=need("COGNITO_USER_POOL_ID"),
            resource_url=resource_url,
            allowed_client_ids=client_ids,
            read_scope=env.get("GATEWAY_READ_SCOPE", "hubara-mcp/ads.read"),
            pii_scope=env.get("GATEWAY_PII_SCOPE", "hubara-mcp/ads.pii"),
            pii_group=env.get("GATEWAY_PII_GROUP", "admin"),
            require_audience=require_audience,
        )
        token_url = env.get("BACKEND_M2M_TOKEN_URL") or f"https://{need('COGNITO_DOMAIN')}/oauth2/token"
        return cls(
            cognito=cognito,
            backend_url=need("BACKEND_URL"),
            m2m_token_url=token_url,
            m2m_client_id=need("BACKEND_M2M_CLIENT_ID"),
            m2m_client_secret=need("BACKEND_M2M_CLIENT_SECRET"),
            m2m_scope=env.get("BACKEND_M2M_SCOPE", "hubara-api/ads.read"),
            allowed_hosts=_list(env.get("GATEWAY_ALLOWED_HOSTS")) or (parts.netloc,),
            allowed_origins=_list(env.get("GATEWAY_ALLOWED_ORIGINS")),
            limits=GatewayLimits(
                max_body_bytes=int(env.get("GATEWAY_MAX_BODY_BYTES", 64 * 1024)),
                rate_limit_per_minute=int(env.get("GATEWAY_RATE_LIMIT_PER_MINUTE", 120)),
            ),
            backend_timeout_s=float(env.get("BACKEND_TIMEOUT_S", 20)),
            backend_max_response_bytes=int(env.get("BACKEND_MAX_RESPONSE_BYTES", 8 * 1024 * 1024)),
            bind=env.get("GATEWAY_BIND", "0.0.0.0"),
            port=int(env.get("GATEWAY_PORT", 8765)),
        )
