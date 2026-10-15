"""Auth del gateway: el MCP server es un *resource server* OAuth 2.1 sobre Cognito.

Acepta SOLO access tokens del user pool emitidos para él (spec MCP 2026-07-28
§Authorization: «MUST validate that access tokens were issued specifically for them»):
- firma RS256 con el JWKS del pool (`kid`), `iss` exacto, `exp`;
- `token_use == "access"` (un ID token nunca autoriza);
- `client_id` ∈ allowlist (el app client `dsh-hubara`; el del dashboard NO);
- `aud` = URL canónica del gateway (resource binding RFC 8707 de managed login).
Nunca reenvía este token a nadie (sin passthrough): al backend va el M2M propio.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import anyio
import jwt
from mcp.server.auth.provider import AccessToken

logger = logging.getLogger(__name__)

KeyResolver = Callable[[str], Awaitable[Any | None]]


@dataclass(frozen=True)
class CognitoSettings:
    region: str
    user_pool_id: str
    resource_url: str
    allowed_client_ids: tuple[str, ...]
    read_scope: str
    pii_scope: str
    pii_group: str
    require_audience: bool = True

    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}"

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"


def _same_resource(a: str, b: str) -> bool:
    return a.rstrip("/") == b.rstrip("/")


class CognitoTokenVerifier:
    """`TokenVerifier` del SDK MCP para access tokens de un user pool de Cognito."""

    def __init__(self, settings: CognitoSettings, *, key_resolver: KeyResolver) -> None:
        self.settings = settings
        self.key_resolver = key_resolver

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            return await self._verify(token)
        except jwt.PyJWTError as exc:
            # Sin el token ni sus claims en el log: solo la clase de error.
            logger.info("token rejected: %s", type(exc).__name__)
            return None

    async def _verify(self, token: str) -> AccessToken | None:
        kid = jwt.get_unverified_header(token).get("kid")
        key = await self.key_resolver(kid) if isinstance(kid, str) else None
        if key is None:
            logger.info("token rejected: unknown kid")
            return None

        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=self.settings.issuer,
            # Los access tokens de Cognito no siempre traen `aud`: se valida abajo.
            options={"require": ["exp", "iss", "token_use", "client_id"], "verify_aud": False},
        )
        if claims["token_use"] != "access":
            logger.info("token rejected: token_use=%s", claims["token_use"])
            return None
        if claims["client_id"] not in self.settings.allowed_client_ids:
            logger.info("token rejected: client not allowed")
            return None

        resource = self._matching_audience(claims.get("aud"))
        if resource is None and (self.settings.require_audience or claims.get("aud") is not None):
            logger.info("token rejected: audience is not this resource")
            return None

        groups = claims.get("cognito:groups") or []
        return AccessToken(
            token=token,
            client_id=claims["client_id"],
            scopes=str(claims.get("scope", "")).split(),
            expires_at=int(claims["exp"]),
            resource=resource,
            subject=claims.get("sub"),
            claims={"iss": claims["iss"], "cognito:groups": list(groups) if isinstance(groups, list) else []},
        )

    def _matching_audience(self, aud: Any) -> str | None:
        candidates = aud if isinstance(aud, list) else [aud]
        for candidate in candidates:
            if isinstance(candidate, str) and _same_resource(candidate, self.settings.resource_url):
                return self.settings.resource_url
        return None


def pii_allowed(token: AccessToken | None, settings: CognitoSettings) -> bool:
    """PII sin enmascarar: scope `ads.pii` Y grupo admin (el claim viene en el access token)."""
    if token is None or settings.pii_scope not in token.scopes:
        return False
    groups = (token.claims or {}).get("cognito:groups") or []
    return settings.pii_group in groups


class JwksKeyResolver:
    """`kid` → clave pública desde el JWKS del pool, cacheado por `PyJWKClient`.

    `PyJWKClient` hace I/O síncrono: corre en un worker thread para no frenar el loop.
    Un `kid` desconocido fuerza un refetch (rotación de claves de Cognito).
    """

    def __init__(self, jwks_url: str, *, timeout: float = 5.0) -> None:
        self._client = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=3600, timeout=timeout)

    async def __call__(self, kid: str) -> Any | None:
        try:
            signing_key = await anyio.to_thread.run_sync(self._client.get_signing_key, kid)
        except jwt.PyJWKClientError as exc:
            logger.warning("jwks lookup failed: %s", type(exc).__name__)
            return None
        return signing_key.key
