"""App HTTP del gateway: Streamable HTTP stateless + resource server OAuth + límites.

Capas (de afuera hacia adentro) sobre `/mcp`:
1. `OAuthResourceMiddleware` — 401/403 con `WWW-Authenticate` (`resource_metadata` + `scope`,
   RFC 6750/9728), publica la identidad en el contextvar del SDK;
2. `RateLimitMiddleware` — ventana fija por principal (`client_id` + `sub`);
3. transporte del SDK — Host/Origin (DNS rebinding), Content-Type, tamaño de body (413).
Rutas públicas: `/healthz` y el Protected Resource Metadata (RFC 9728).
"""

import hashlib
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import mcp_types as types
from mcp.server.auth.middleware.auth_context import auth_context_var, get_access_token
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.routes import build_resource_metadata_url, create_protected_resource_routes
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from hubara_gateway.auth import CognitoSettings, pii_allowed
from hubara_gateway.backend import Backend
from hubara_gateway.catalog import TOOLS
from hubara_gateway.schemas import output_schema
from hubara_gateway.tools import call_tool

logger = logging.getLogger(__name__)

MCP_PATH = "/mcp"
VERSION = "0.1.0"


@dataclass(frozen=True)
class GatewayLimits:
    max_body_bytes: int = 64 * 1024
    rate_limit_per_minute: int = 120


# Ninguna tool escribe ni borra; repetir una llamada da lo mismo; el mundo es el backend de Hubara.
_READ_ONLY = types.ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)


def _tool_definitions() -> list[types.Tool]:
    tools = []
    for spec in TOOLS.values():
        input_schema: dict[str, Any] = {
            "type": "object",
            "properties": {arg: {"type": "string"} for arg in spec.arguments},
            "additionalProperties": False,
        }
        if spec.required:
            input_schema["required"] = list(spec.required)
        tools.append(
            types.Tool(
                name=spec.name,
                title=spec.title,
                description=spec.description,
                input_schema=input_schema,
                output_schema=output_schema(spec.name),
                annotations=_READ_ONLY,
            )
        )
    return tools


def build_mcp_server(backend: Backend, cognito: CognitoSettings) -> Server:
    definitions = _tool_definitions()

    async def on_list_tools(ctx: Any, params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
        return types.ListToolsResult(tools=definitions)

    async def on_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        token = get_access_token()
        return await call_tool(
            params.name,
            params.arguments or {},
            backend,
            subject=token.subject if token else None,
            pii_allowed=pii_allowed(token, cognito),
        )

    return Server(
        "hubara-agent-gateway",
        version=VERSION,
        title="Hubara Agent Gateway",
        instructions="Datos de solo lectura del menú Ads de Hubara (Meta Ads + chats atribuidos).",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


def _principal(token: AccessToken) -> str:
    return f"{token.client_id}:{token.subject or '-'}"


def _redact(principal: str) -> str:
    """Huella corta para logs: correlaciona sin escribir identificadores."""
    return hashlib.sha256(principal.encode()).hexdigest()[:12]


_PUBLIC_PATHS = frozenset({"/healthz"})
_PRM_PREFIX = "/.well-known/oauth-protected-resource"


def _is_public(path: str) -> bool:
    return path in _PUBLIC_PATHS or path == _PRM_PREFIX or path.startswith(_PRM_PREFIX + "/")


class OAuthResourceMiddleware:
    """Resource server OAuth 2.1 (spec MCP §Authorization, RFC 6750, RFC 9728): todo menos health y PRM."""

    def __init__(self, app: ASGIApp, *, verifier: TokenVerifier, required_scope: str, resource_metadata_url: str) -> None:
        self.app = app
        self.verifier = verifier
        self.required_scope = required_scope
        self.resource_metadata_url = resource_metadata_url

    def _challenge(self, error: str | None = None, description: str | None = None) -> str:
        parts = []
        if error:
            parts += [f'error="{error}"', f'error_description="{description}"']
        parts += [f'resource_metadata="{self.resource_metadata_url}"', f'scope="{self.required_scope}"']
        return "Bearer " + ", ".join(parts)

    async def _deny(self, scope: Scope, receive: Receive, send: Send, status: int, error: str | None, description: str) -> None:
        body = {"error": error or "unauthorized", "error_description": description}
        response = JSONResponse(body, status_code=status, headers={"www-authenticate": self._challenge(error, description)})
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Deny-by-default: una ruta nueva nace protegida; públicas solo health y PRM.
        if scope["type"] != "http" or _is_public(scope["path"]):
            await self.app(scope, receive, send)
            return

        header = dict(scope["headers"]).get(b"authorization", b"").decode("latin-1")
        if not header.lower().startswith("bearer ") or not header[7:].strip():
            # RFC 6750 §3.1: sin credenciales, sin código de error.
            await self._deny(scope, receive, send, 401, None, "Authentication required")
            return

        token = await self.verifier.verify_token(header[7:].strip())
        if token is None:
            await self._deny(scope, receive, send, 401, "invalid_token", "The access token is invalid or not for this resource")
            return
        if self.required_scope not in token.scopes:
            await self._deny(scope, receive, send, 403, "insufficient_scope", f"Required scope: {self.required_scope}")
            return

        scope["user"] = AuthenticatedUser(token)
        reset = auth_context_var.set(scope["user"])
        try:
            await self.app(scope, receive, send)
        finally:
            auth_context_var.reset(reset)


class RateLimitMiddleware:
    """Ventana fija de 60 s por principal autenticado (en memoria: una réplica)."""

    def __init__(self, app: ASGIApp, *, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.app = app
        self.per_minute = per_minute
        self.clock = clock
        self._windows: dict[str, tuple[int, int]] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        user = scope.get("user") if scope["type"] == "http" else None
        if not isinstance(user, AuthenticatedUser):
            await self.app(scope, receive, send)
            return

        key = _principal(user.access_token)
        window = int(self.clock() // 60)
        current, count = self._windows.get(key, (window, 0))
        count = count + 1 if current == window else 1
        self._windows[key] = (window, count)
        if len(self._windows) > 10_000:  # cota de memoria: descarta ventanas viejas
            self._windows = {k: v for k, v in self._windows.items() if v[0] == window}

        if count > self.per_minute:
            logger.warning("rate_limited principal=%s", _redact(key))
            retry = str(int(60 - self.clock() % 60) or 1)
            response = JSONResponse({"error": "rate_limited"}, status_code=429, headers={"retry-after": retry})
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


async def _healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def create_app(
    *,
    cognito: CognitoSettings,
    verifier: TokenVerifier,
    backend: Backend,
    allowed_origins: Iterable[str],
    allowed_hosts: Iterable[str],
    limits: GatewayLimits,
) -> Starlette:
    resource_url = AnyHttpUrl(cognito.resource_url)
    routes: list[Route] = [Route("/healthz", _healthz, methods=["GET"])]
    routes += create_protected_resource_routes(
        resource_url=resource_url,
        authorization_servers=[AnyHttpUrl(cognito.issuer)],
        scopes_supported=[cognito.read_scope, cognito.pii_scope],
        resource_name="Hubara Agent Gateway",
    )

    app = build_mcp_server(backend, cognito).streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=True,
        stateless_http=True,
        max_request_body_size=limits.max_body_bytes,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(allowed_hosts),
            allowed_origins=list(allowed_origins),
        ),
        host="0.0.0.0",
        custom_starlette_routes=routes,
    )
    # add_middleware antepone: el último agregado es el más externo (auth → rate limit → MCP).
    app.add_middleware(RateLimitMiddleware, per_minute=limits.rate_limit_per_minute)
    app.add_middleware(
        OAuthResourceMiddleware,
        verifier=verifier,
        required_scope=cognito.read_scope,
        resource_metadata_url=str(build_resource_metadata_url(resource_url)),
    )
    return app
