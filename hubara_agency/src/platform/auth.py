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

import time
from functools import lru_cache
from typing import Any

import structlog
from fastapi import HTTPException, Request

from src.platform import config, sse_ticket

logger = structlog.get_logger()


def _sse_ticket_secret() -> str:
    """Secreto para firmar/verificar tickets SSE. Reusa el service token M2M
    (compartido entre workers, garantizado en prod por el preflight de SEC-01).
    En dev cae a un valor fijo (la auth es no-op igual)."""
    if not config.is_placeholder(config.HUBARA_SERVICE_TOKEN):
        return config.HUBARA_SERVICE_TOKEN
    return "dev-sse-ticket-secret"


#: Vida de un ticket SSE (segundos). Corto: solo tiene que sobrevivir el
#: round-trip mint→abrir EventSource. No es un bearer reutilizable.
SSE_TICKET_TTL_SECONDS = 30


def mint_sse_ticket(ttl_seconds: int = SSE_TICKET_TTL_SECONDS) -> str:
    """Emite un ticket SSE firmado. Lo llama el endpoint POST (autenticado por
    header) para que el frontend abra `/events` sin poner el token en la URL."""
    return sse_ticket.mint(_sse_ticket_secret(), ttl_seconds, time.time())


def _cognito_configured() -> bool:
    """True si hay pool + app client REALES → la auth se enforcea (prod).

    El placeholder de Terraform (`SSM_PLACEHOLDER`) cuenta como NO configurado:
    validar tokens contra un pool placeholder solo produce 401s confusos.
    """
    return not config.is_placeholder(
        config.COGNITO_USER_POOL_ID
    ) and not config.is_placeholder(config.COGNITO_APP_CLIENT_ID)


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


#: Header con el ID token de Cognito que manda el dashboard (C-6). Solo
#: ENRIQUECE el actor del registro de cambios: la auth la decide el access
#: token, y un ID token malo jamás tumba el request.
ID_TOKEN_HEADER = "X-Hubara-Id-Token"


def _verify_id_token(token: str) -> dict[str, Any]:
    """Valida un ID token de Cognito: firma (JWKS del pool), issuer, ``aud``
    = app client del tenant, ``token_use == "id"`` y vigencia. Levanta si es
    inválido."""
    import jwt

    issuer = _issuer()
    signing_key = _jwk_client(
        f"{issuer}/.well-known/jwks.json"
    ).get_signing_key_from_jwt(token)
    # A diferencia del access token, el ID token SÍ trae ``aud`` (= client id).
    claims: dict[str, Any] = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=issuer,
        audience=config.COGNITO_APP_CLIENT_ID,
        options={"require": ["exp", "iss", "aud", "sub"]},
    )
    if claims.get("token_use") != "id":
        raise ValueError("el token no es un ID token de Cognito")
    return claims


def _cognito_actor(request: Any, access_claims: dict[str, Any]) -> str:
    """El actor de un request autenticado con Cognito.

    Con ``username_attributes=["email"]`` el ``username`` del access token es
    un UUID opaco: si el request trae el ID token de la MISMA persona (mismo
    ``sub``) y es válido, el actor es su ``email``. Cualquier problema con el
    ID token se ignora (actor = ``username``/``sub``)."""
    fallback = str(
        access_claims.get("username") or access_claims.get("sub") or UNKNOWN_ACTOR
    )
    headers = getattr(request, "headers", None) or {}
    id_token = str(headers.get(ID_TOKEN_HEADER) or "").strip()
    if not id_token:
        return fallback
    try:
        id_claims = _verify_id_token(id_token)
    except Exception as exc:  # noqa: BLE001 — el ID token nunca rechaza el request
        logger.warning("auth_id_token_ignored", reason=type(exc).__name__)
        return fallback
    sub = access_claims.get("sub")
    email = id_claims.get("email")
    if not sub or id_claims.get("sub") != sub:
        logger.warning("auth_id_token_ignored", reason="sub_mismatch")
        return fallback
    if not isinstance(email, str) or not email.strip():
        return fallback
    return email.strip()


def _extract_header_token(request: Request) -> str:
    """Bearer SOLO del header ``Authorization`` ("" si no hay).

    Es la única fuente aceptada para el **service token** M2M: un secreto de
    larga vida NUNCA debe viajar por query string (queda en access logs /
    proxies / referrer). El JWT de Cognito del SSE sí puede venir por query
    (`_extract_token`), porque es de corta vida y el `EventSource` no puede
    mandar headers.
    """
    authz = request.headers.get("Authorization", "")
    scheme, _, header_token = authz.partition(" ")
    if scheme.lower() == "bearer" and header_token.strip():
        return header_token.strip()
    return ""


def _extract_token(request: Request) -> str:
    """Bearer del header ``Authorization``, o ``access_token`` de query (para SSE).

    El ``EventSource`` del browser no puede setear headers custom, así que el
    stream (``/api/dashboard/events``) manda el access-token por query param. El
    resto de la API usa el header. Header tiene precedencia.
    """
    return _extract_header_token(request) or (
        request.query_params.get("access_token") or ""
    ).strip()


#: Nombre del atributo en `request.state` donde `require_auth` deja QUIÉN
#: pasó la auth (usuario Cognito, "service" o "local" en dev).
ACTOR_STATE_KEY = "hubara_actor"
#: Sin auth en la ruta no hay actor: se dice, no se inventa.
UNKNOWN_ACTOR = "desconocido"


def _set_actor(request: Any, actor: str) -> None:
    state = getattr(request, "state", None)
    if state is not None:
        setattr(state, ACTOR_STATE_KEY, actor)


def current_actor(request: Any) -> str:
    """Quién hace este request, VERIFICADO por `require_auth`.

    Es la identidad para registros de cambios (central de cupones, D6): el
    `email` del ID token de Cognito de la misma persona (C-6) o, sin él, el
    `username` del access token (o su `sub`); ``"service"`` para el token
    M2M, ``"local"`` en dev sin Cognito. Nunca sale del cuerpo del request.
    Sin `require_auth` en la ruta devuelve ``"desconocido"``.
    """
    state = getattr(request, "state", None)
    actor = getattr(state, ACTOR_STATE_KEY, None) if state is not None else None
    return actor if isinstance(actor, str) and actor else UNKNOWN_ACTOR


def require_auth(request: Request) -> None:
    """Dependency: exige auth válida para las rutas del dashboard.

    Orden de resolución:

    1. **Service token** (machine-to-machine: workers → API, ej. el executor
       de order-sentinel — un worker no tiene request entrante del cual portar
       identidad, castkit no aplica). Si está CONFIGURADO y el bearer matchea
       (tiempo constante), pasa — independiente de Cognito.
    2. **Cognito no configurado**:
         - en producción → **FAIL-CLOSED** con ``503`` (SEC-01): faltar
           ``COGNITO_*`` NO degrada a no-op; la API rehúsa servir en vez de
           exponer PII sin auth (incidente "API SIN auth").
         - en dev/tests → no-op (local sin Cognito sigue andando).
    3. **Cognito configurado** → exige y valida el access-token (header o
       query). Sin/invalid token → ``401``.
    """
    import secrets

    # 1. Service token interno — SOLO por header (nunca query: es un secreto de
    #    larga vida que no debe quedar en logs/URLs). El placeholder de Terraform
    #    NO cuenta como token (bearer adivinable = bypass). Env vacío tampoco.
    header_token = _extract_header_token(request)
    service_token = config.HUBARA_SERVICE_TOKEN
    if (
        not config.is_placeholder(service_token)
        and header_token
        and secrets.compare_digest(header_token, service_token)
    ):
        _set_actor(request, "service")
        return

    # 1b. Ticket SSE (SEC-06): el stream `/events` no puede mandar header, así
    #     que el frontend pasa un ticket firmado de corta vida por query — nunca
    #     el access-token. Solo válido para el path del stream.
    if request.url.path.endswith("/events"):
        ticket = request.query_params.get("ticket", "")
        if ticket and sse_ticket.verify(_sse_ticket_secret(), ticket, time.time()):
            return

    # 2. Cognito no configurado → fail-closed en prod, no-op en dev/tests.
    if not _cognito_configured():
        if config.is_production():
            logger.error(
                "auth_misconfigured_in_prod",
                reason="COGNITO_* ausente/placeholder en producción — 503 fail-closed",
                path=request.url.path,
            )
            raise HTTPException(
                status_code=503,
                detail="Auth no configurada: COGNITO_* ausente en producción",
            )
        _set_actor(request, "local")
        return

    # 3. Cognito configurado → validar el access-token (header o query para SSE).
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Falta el bearer token")
    try:
        claims = _verify_token(token)
    except HTTPException:
        raise
    except Exception as exc:  # firma / exp / issuer / claims inválidos
        raise HTTPException(status_code=401, detail="Token inválido") from exc
    _set_actor(request, _cognito_actor(request, claims))
