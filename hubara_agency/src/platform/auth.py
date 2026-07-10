"""Auth de la API del dashboard — validación de JWT de Cognito.

Diseño (PENDING_IMPLEMENTATION §2):

- ``require_auth`` es una dependency de FastAPI que ``main.py`` cuelga de los
  routers del DASHBOARD (no de los webhooks de Meta, que tienen su propia auth:
  ``hub.verify_token`` + ``X-Hub-Signature-256``).
- **Enforce-only-when-configured**: si Cognito no está configurado
  (``COGNITO_USER_POOL_ID`` / ``COGNITO_APP_CLIENT_ID`` vacíos) la dependency es
  NO-OP. Así el dev local y los tests existentes (que pegan rutas dashboard sin
  token) siguen andando. En prod, SSM setea ambos → enforce.
- Valida el ACCESS token de Cognito contra el JWKS del pool: firma RS256 +
  issuer + ``client_id`` + ``token_use == "access"`` + exp. El JWKS se cachea.

Sin token / token inválido → ``401``.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import HTTPException, Request

from src.platform import config


def _cognito_configured() -> bool:
    """True si hay pool + app client → la auth se enforcea (prod)."""
    return bool(config.COGNITO_USER_POOL_ID and config.COGNITO_APP_CLIENT_ID)


def _issuer() -> str:
    return (
        f"https://cognito-idp.{config.AWS_REGION}.amazonaws.com/"
        f"{config.COGNITO_USER_POOL_ID}"
    )


@lru_cache(maxsize=4)
def _jwk_client(jwks_uri: str):  # noqa: ANN202 — PyJWKClient (import perezoso)
    """Cliente JWKS cacheado (un fetch del set de llaves del pool por uri).

    Import perezoso de pyjwt: ``auth`` queda importable aunque pyjwt no esté
    presente (la rama no-op / los tests que mockean ``_verify_token`` nunca lo
    importan). En prod la lib está declarada en pyproject.
    """
    from jwt import PyJWKClient

    return PyJWKClient(jwks_uri)


def _verify_token(token: str) -> dict[str, Any]:
    """Valida un ACCESS token de Cognito. Levanta si es inválido."""
    import jwt

    issuer = _issuer()
    signing_key = _jwk_client(
        f"{issuer}/.well-known/jwks.json"
    ).get_signing_key_from_jwt(token)
    # Los ACCESS tokens de Cognito NO traen ``aud`` (traen ``client_id``); por eso
    # ``verify_aud=False`` + chequeo manual de ``client_id`` + ``token_use``.
    claims: dict[str, Any] = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=issuer,
        options={"verify_aud": False},
    )
    if claims.get("client_id") != config.COGNITO_APP_CLIENT_ID:
        raise ValueError("client_id no coincide con el app client del tenant")
    if claims.get("token_use") != "access":
        raise ValueError("el token no es un access token de Cognito")
    return claims


def _extract_token(request: Request) -> str:
    """Bearer del header ``Authorization``, o ``access_token`` de query (para SSE).

    El ``EventSource`` del browser no puede setear headers custom, así que el
    stream (``/api/dashboard/events``) manda el access-token por query param. El
    resto de la API usa el header. Header tiene precedencia.
    """
    authz = request.headers.get("Authorization", "")
    scheme, _, header_token = authz.partition(" ")
    if scheme.lower() == "bearer" and header_token.strip():
        return header_token.strip()
    return (request.query_params.get("access_token") or "").strip()


def require_auth(request: Request) -> None:
    """Dependency: exige un access-token válido de Cognito (header o query).

    No-op si Cognito no está configurado (dev/tests). Sin/invalid token → 401.
    """
    if not _cognito_configured():
        return
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Falta el bearer token")
    # Service token interno (machine-to-machine: workers → API, ej. el
    # executor de order-sentinel — un worker no tiene request entrante del
    # cual portar identidad, castkit no aplica). Solo si está CONFIGURADO
    # (env vacío = camino Cognito intacto, fail-closed) y en tiempo
    # constante. Un service token errado cae al camino Cognito normal.
    import secrets

    if config.HUBARA_SERVICE_TOKEN and secrets.compare_digest(
        token, config.HUBARA_SERVICE_TOKEN
    ):
        return
    try:
        _verify_token(token)
    except HTTPException:
        raise
    except Exception as exc:  # firma / exp / issuer / claims inválidos
        raise HTTPException(status_code=401, detail="Token inválido") from exc
