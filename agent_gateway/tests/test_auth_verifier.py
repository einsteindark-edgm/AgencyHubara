"""Verificador de access tokens de Cognito para el gateway (resource server MCP).

Exige: firma RS256 del JWKS del pool, `iss` exacto, `exp`, `token_use=access`,
`client_id` en la allowlist y `aud` = URL canónica del gateway (RFC 8707 vía resource
binding de managed login). Nunca acepta un ID token ni un token de otro cliente/recurso.
"""

import time

import jwt
import pytest

from hubara_gateway.auth import CognitoTokenVerifier, pii_allowed
from tests.auth_helpers import CLIENT, DROP, ISSUER, PII, READ, RESOURCE, Issuer, StaticKeys, settings


@pytest.fixture
def issuer() -> Issuer:
    return Issuer()


@pytest.fixture
def verifier(issuer: Issuer) -> CognitoTokenVerifier:
    return CognitoTokenVerifier(settings(), key_resolver=StaticKeys(issuer))


async def test_valid_access_token_is_accepted_with_identity(verifier: CognitoTokenVerifier, issuer: Issuer) -> None:
    token = await verifier.verify_token(issuer.token())

    assert token is not None
    assert token.client_id == CLIENT
    assert token.subject == "user-sub-1"
    assert token.resource == RESOURCE
    assert READ in token.scopes
    assert token.expires_at is not None


@pytest.mark.parametrize(
    ("case", "overrides"),
    [
        ("expired", {"exp": int(time.time()) - 10}),
        ("wrong_issuer", {"iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Other"}),
        ("id_token", {"token_use": "id"}),
        ("missing_token_use", {"token_use": DROP}),
        ("dashboard_client", {"client_id": "dashboardclient000000000"}),
        ("missing_client_id", {"client_id": DROP}),
        ("other_resource", {"aud": "https://evil.example.com/mcp"}),
        ("no_audience", {"aud": DROP}),
        ("missing_exp", {"exp": DROP}),
    ],
)
async def test_token_rejected(verifier: CognitoTokenVerifier, issuer: Issuer, case: str, overrides: dict) -> None:
    assert await verifier.verify_token(issuer.token(**overrides)) is None, case


async def test_token_signed_by_unknown_key_is_rejected(verifier: CognitoTokenVerifier) -> None:
    stranger = Issuer(kid="kid-1")  # mismo kid, otra clave

    assert await verifier.verify_token(stranger.token()) is None


async def test_token_with_unknown_kid_is_rejected(verifier: CognitoTokenVerifier) -> None:
    assert await verifier.verify_token(Issuer(kid="kid-unknown").token()) is None


async def test_unsigned_and_hmac_tokens_are_rejected(verifier: CognitoTokenVerifier) -> None:
    claims = {"iss": ISSUER, "client_id": CLIENT, "token_use": "access", "aud": RESOURCE, "exp": 9999999999}
    unsigned = jwt.encode(claims, key=None, algorithm="none", headers={"kid": "kid-1"})
    hmac = jwt.encode(claims, key="secret" * 8, algorithm="HS256", headers={"kid": "kid-1"})

    assert await verifier.verify_token(unsigned) is None
    assert await verifier.verify_token(hmac) is None


async def test_garbage_token_is_rejected(verifier: CognitoTokenVerifier) -> None:
    assert await verifier.verify_token("not-a-jwt") is None


async def test_audience_list_containing_the_resource_is_accepted(issuer: Issuer, verifier: CognitoTokenVerifier) -> None:
    token = await verifier.verify_token(issuer.token(aud=["https://other.example.com", RESOURCE]))

    assert token is not None
    assert token.resource == RESOURCE


async def test_without_require_audience_a_token_lacking_aud_is_accepted_but_wrong_aud_is_not(issuer: Issuer) -> None:
    lax = CognitoTokenVerifier(settings(require_audience=False), key_resolver=StaticKeys(issuer))

    assert await lax.verify_token(issuer.token(aud=DROP)) is not None
    assert await lax.verify_token(issuer.token(aud="https://evil.example.com/mcp")) is None


async def test_pii_requires_scope_and_admin_group(verifier: CognitoTokenVerifier, issuer: Issuer) -> None:
    both = await verifier.verify_token(issuer.token(scope=f"{READ} {PII}", **{"cognito:groups": ["admin"]}))
    scope_only = await verifier.verify_token(issuer.token(scope=f"{READ} {PII}", **{"cognito:groups": ["operarios"]}))
    group_only = await verifier.verify_token(issuer.token(**{"cognito:groups": ["admin"]}))

    assert pii_allowed(both, settings()) is True
    assert pii_allowed(scope_only, settings()) is False
    assert pii_allowed(group_only, settings()) is False
