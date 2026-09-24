"""Actor del registro de cambios con el ID token de Cognito (C-6).

El pool usa el email como username: el `username` del ACCESS token es un
UUID opaco, así que el registro de la central de cupones decía "3f9c…" en
vez de quién fue. El dashboard manda además `X-Hubara-Id-Token`; si ese ID
token es válido (firma del JWKS del pool, issuer, `aud` = app client,
`token_use == "id"`, vigente) y es de la MISMA persona (`sub` igual al del
access token), el actor es su `email`. Cualquier problema con el ID token se
ignora (actor = `username`/`sub`): NUNCA tumba el request.

Tokens firmados de verdad con una llave RSA local y su JWKS (sin red): se
ejercita la verificación real, no un mock de `_verify_token`.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from src.platform import auth, config

_REGION = "us-east-1"
_POOL = "us-east-1_TESTPOOL"
_CLIENT = "app-client-1"
_ISSUER = f"https://cognito-idp.{_REGION}.amazonaws.com/{_POOL}"
_SUB = "3f9c2b7e-0000-4000-8000-000000000001"


def _rsa_key() -> Any:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


_POOL_KEY = _rsa_key()
_OTHER_KEY = _rsa_key()  # un atacante firmando con SU llave


class _LocalJWKS:
    """Como `PyJWKClient`, pero con el JWKS en memoria (sin red)."""

    def __init__(self, jwks: dict[str, Any]) -> None:
        self._keys = jwt.PyJWKSet.from_dict(jwks).keys

    def get_signing_key_from_jwt(self, token: str) -> Any:
        kid = jwt.get_unverified_header(token).get("kid")
        for key in self._keys:
            if key.key_id == kid:
                return key
        raise jwt.PyJWKClientError(f"kid {kid!r} no está en el JWKS")


def _jwks() -> dict[str, Any]:
    public = jwt.algorithms.RSAAlgorithm.to_jwk(_POOL_KEY.public_key(), as_dict=True)
    return {"keys": [{**public, "kid": "pool-kid", "alg": "RS256", "use": "sig"}]}


def _sign(claims: dict[str, Any], *, key: Any = _POOL_KEY, kid: str = "pool-kid") -> str:
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def _access(**over: Any) -> str:
    now = int(time.time())
    claims = {
        "sub": _SUB, "iss": _ISSUER, "client_id": _CLIENT, "token_use": "access",
        "username": _SUB, "iat": now, "exp": now + 3600,
    }
    return _sign({**claims, **over})


def _id(*, key: Any = _POOL_KEY, kid: str = "pool-kid", **over: Any) -> str:
    now = int(time.time())
    claims = {
        "sub": _SUB, "iss": _ISSUER, "aud": _CLIENT, "token_use": "id",
        "email": "ana.perez@example.com", "cognito:username": _SUB,
        "iat": now, "exp": now + 3600,
    }
    return _sign({**claims, **over}, key=key, kid=kid)


@pytest.fixture(autouse=True)
def _cognito_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "AWS_REGION", _REGION)
    monkeypatch.setattr(config, "COGNITO_USER_POOL_ID", _POOL)
    monkeypatch.setattr(config, "COGNITO_APP_CLIENT_ID", _CLIENT)
    monkeypatch.setattr(config, "HUBARA_SERVICE_TOKEN", "secreto-interno-123")
    jwks = _LocalJWKS(_jwks())
    monkeypatch.setattr(auth, "_jwk_client", lambda uri: jwks)


def _request(access: str, id_token: str | None = None) -> SimpleNamespace:
    headers = {"Authorization": f"Bearer {access}"}
    if id_token is not None:
        headers["X-Hubara-Id-Token"] = id_token
    return SimpleNamespace(
        headers=headers,
        query_params={},
        url=SimpleNamespace(path="/api/marketing/coupons"),
        state=SimpleNamespace(),
    )


def test_valid_id_token_of_the_same_user_makes_the_actor_its_email() -> None:
    request = _request(_access(), _id())

    auth.require_auth(request)

    assert auth.current_actor(request) == "ana.perez@example.com"


def test_without_id_token_the_actor_stays_the_access_token_username() -> None:
    request = _request(_access())

    auth.require_auth(request)

    assert auth.current_actor(request) == _SUB


@pytest.mark.parametrize(
    "bad_id_token",
    [
        pytest.param(lambda: _id(sub="otra-persona"), id="sub-de-otra-persona"),
        pytest.param(lambda: _id(key=_OTHER_KEY), id="firma-falsa"),
        pytest.param(lambda: _id(kid="kid-desconocido"), id="kid-fuera-del-jwks"),
        pytest.param(lambda: _id(exp=int(time.time()) - 60), id="vencido"),
        pytest.param(lambda: _id(aud="otro-app-client"), id="aud-de-otro-cliente"),
        pytest.param(lambda: _id(iss="https://cognito-idp.us-east-1.amazonaws.com/otro"), id="otro-pool"),
        pytest.param(lambda: _id(token_use="access"), id="no-es-id-token"),
        pytest.param(lambda: _id(email=""), id="sin-email"),
        pytest.param(lambda: "no-es-un-jwt", id="basura"),
    ],
)
def test_any_problem_with_the_id_token_is_ignored_and_never_rejects(bad_id_token) -> None:
    request = _request(_access(), bad_id_token())

    auth.require_auth(request)  # no levanta: el ID token solo enriquece el actor

    assert auth.current_actor(request) == _SUB


def test_the_access_token_is_still_what_authenticates() -> None:
    """Un ID token válido NO reemplaza al access token: sin access válido, 401."""
    from fastapi import HTTPException

    request = _request(_access(exp=int(time.time()) - 60), _id())

    with pytest.raises(HTTPException) as err:
        auth.require_auth(request)

    assert err.value.status_code == 401
