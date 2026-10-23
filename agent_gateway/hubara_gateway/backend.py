"""Cliente HTTP de solo lectura hacia el backend `/api/ads/*`.

Sin token passthrough: al backend va el token `client_credentials` PROPIO del gateway
(app client M2M de Cognito, scope `hubara-api/ads.read`). El token del usuario nunca
sale del gateway; su `sub` viaja solo como `X-Hubara-On-Behalf-Of` para auditoría.
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import anyio
import httpx

logger = logging.getLogger(__name__)

ADS_PREFIX = "/api/ads/"
AUDIT_HEADER = "x-hubara-on-behalf-of"
_EXPIRY_MARGIN_S = 60


@dataclass(frozen=True)
class BackendResponse:
    status: int
    body: Any


class BackendUnavailable(RuntimeError):
    """El backend no respondió (red, timeout, respuesta demasiado grande, sin token M2M)."""


class Backend(Protocol):
    async def get(self, path: str, query: dict[str, str], *, on_behalf_of: str | None = None) -> BackendResponse: ...


class M2MTokenProvider:
    """Access token `client_credentials` de Cognito, cacheado hasta `exp - 60 s`.

    Cognito cobra cada emisión M2M: el caché + el lock (una sola emisión concurrente)
    dejan ~24 emisiones/día con tokens de 1 h.
    """

    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        scope: str,
        http: httpx.AsyncClient,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._token_url = token_url
        self._auth = httpx.BasicAuth(client_id, client_secret)
        self._scope = scope
        self._http = http
        self._clock = clock
        self._lock = anyio.Lock()
        self._token: str | None = None
        self._valid_until = 0.0

    def invalidate(self) -> None:
        self._token = None

    async def token(self) -> str:
        async with self._lock:
            if self._token is None or self._clock() >= self._valid_until:
                await self._refresh()
            assert self._token is not None
            return self._token

    async def _refresh(self) -> None:
        try:
            response = await self._http.post(
                self._token_url,
                data={"grant_type": "client_credentials", "scope": self._scope},
                auth=self._auth,
            )
        except httpx.HTTPError as exc:
            raise BackendUnavailable(f"m2m_token: {type(exc).__name__}") from exc
        if response.status_code != 200:
            # El body puede describir el client; solo el status al log/mensaje.
            raise BackendUnavailable(f"m2m_token: token endpoint answered {response.status_code}")
        payload = response.json()
        self._token = payload["access_token"]
        self._valid_until = self._clock() + int(payload.get("expires_in", 3600)) - _EXPIRY_MARGIN_S
        logger.info("m2m token refreshed")


class HttpBackend:
    """`Backend` real: GET a `/api/ads/*` con el token M2M, timeouts y tope de tamaño."""

    def __init__(
        self,
        *,
        base_url: str,
        tokens: M2MTokenProvider,
        http: httpx.AsyncClient,
        max_response_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._tokens = tokens
        self._http = http
        self._max_bytes = max_response_bytes

    async def get(self, path: str, query: dict[str, str], *, on_behalf_of: str | None = None) -> BackendResponse:
        if not path.startswith(ADS_PREFIX):
            raise ValueError(f"the gateway only reads {ADS_PREFIX}*")
        response = await self._send(path, query, on_behalf_of)
        if response.status == 401:
            # Token revocado/rotado antes de su exp: uno nuevo, una sola vez.
            self._tokens.invalidate()
            response = await self._send(path, query, on_behalf_of)
        return response

    async def _send(self, path: str, query: dict[str, str], on_behalf_of: str | None) -> BackendResponse:
        headers = {"authorization": f"Bearer {await self._tokens.token()}", "accept": "application/json"}
        if on_behalf_of:
            headers[AUDIT_HEADER] = on_behalf_of
        try:
            async with self._http.stream("GET", self._base_url + path, params=query, headers=headers) as response:
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self._max_bytes:
                        raise BackendUnavailable(f"backend response too large (> {self._max_bytes} bytes)")
                    chunks.append(chunk)
        except httpx.TimeoutException as exc:
            raise BackendUnavailable("backend timeout") from exc
        except httpx.HTTPError as exc:
            raise BackendUnavailable(f"backend unreachable: {type(exc).__name__}") from exc

        raw = b"".join(chunks)
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = None
        return BackendResponse(response.status_code, body)
