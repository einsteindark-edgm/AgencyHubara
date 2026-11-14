"""Emisor de JWT de prueba con la forma de un access token de Cognito (claves RSA locales)."""

import time
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from hubara_gateway.auth import CognitoSettings

REGION = "us-east-1"
POOL = "us-east-1_TestPool1"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}"
RESOURCE = "http://localhost:8765/mcp"
CLIENT = "dshhubaraclient0000000000"
READ = "hubara-mcp/ads.read"
PII = "hubara-mcp/ads.pii"


def settings(**overrides: Any) -> CognitoSettings:
    base = {
        "region": REGION,
        "user_pool_id": POOL,
        "resource_url": RESOURCE,
        "allowed_client_ids": (CLIENT,),
        "read_scope": READ,
        "pii_scope": PII,
        "pii_group": "admin",
        "require_audience": True,
    }
    return CognitoSettings(**{**base, **overrides})


class Issuer:
    def __init__(self, kid: str = "kid-1") -> None:
        self.kid = kid
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    @property
    def public_key(self) -> Any:
        return self.key.public_key()

    def token(self, **overrides: Any) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "sub": "user-sub-1",
            "iss": ISSUER,
            "client_id": CLIENT,
            "token_use": "access",
            "scope": f"openid {READ}",
            "aud": RESOURCE,
            "auth_time": now,
            "iat": now,
            "exp": now + 3600,
            "jti": "jti-1",
            "username": "operador@example.com",
        }
        drop = {k for k, v in overrides.items() if v is _DROP}
        claims.update({k: v for k, v in overrides.items() if v is not _DROP})
        for k in drop:
            claims.pop(k, None)
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": self.kid})


class _Drop:
    pass


_DROP = _Drop()
DROP: Any = _DROP


class StaticKeys:
    """`key_resolver` de prueba: kid → clave pública (en prod, el JWKS del pool)."""

    def __init__(self, *issuers: Issuer) -> None:
        self.keys = {i.kid: i.public_key for i in issuers}

    async def __call__(self, kid: str) -> Any | None:
        return self.keys.get(kid)
