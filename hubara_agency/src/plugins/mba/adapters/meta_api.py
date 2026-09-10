"""Base común de la Meta Business Agent Cloud API (D1.6; la reutiliza D2.1).

Host ``MBA_API_BASE_URL`` (definido en el dominio; NO el host del Graph API: es otra
superficie, con su propio ``X-API-Version`` por endpoint). Token de system
user en ``META_MBA_TOKEN`` (SSM): sin él, o con el placeholder, el adapter
NO llama a nadie (``not_configured``).

Errores en tres clases cerradas, para que el use case decida sin mirar HTTP:

* ``not_configured`` — falta el token; nada se llamó.
* ``rejected`` — 4xx (salvo 429): Meta no acepta la operación (p.ej. soltar
  un hilo que no tenemos). No se reintenta; ``detail`` trae el mensaje.
* ``unavailable`` — 5xx / 429 tras ``MAX_ATTEMPTS`` con backoff (360dialog:
  "cualquier endpoint puede devolver 4xx/500").
* ``ambiguous`` — timeout / error de red en un POST NO idempotente
  (``retry_on_transport_error=False``): Meta pudo haber procesado el pedido
  (lección L-1 de CAPI: un timeout no es "no pasó"). No se reintenta — un
  segundo POST daría 4xx y taparía el éxito del primero; el caller lo trata
  como "posiblemente hecho" hasta que Meta confirme.

Latencia peor caso: ``MAX_ATTEMPTS × timeout + backoff`` (≈31,5 s con los
defaults). Aceptable en el plano de gestión; al correr dentro de una
activity de Temporal (D1.7 / D1.9) necesita ``@with_heartbeat`` (R-HEARTBEAT).
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable

from src.plugins.mba.domain.config import MBA_BASE_URL
from src.sdk.runtime import is_placeholder

__all__ = [
    "DEFAULT_TIMEOUT_S",
    "MAX_ATTEMPTS",
    "MBA_API_BASE_URL",
    "TOKEN_ENV",
    "MbaApiError",
    "mba_token",
    "post_json",
    "request_json",
]

#: Host único de la Cloud API de MBA. Vive en el dominio (``config.py`` arma
#: los requests literales del preview con él); acá solo se re-exporta para
#: que los adapters y sus tests hablen del mismo valor.
MBA_API_BASE_URL = MBA_BASE_URL
TOKEN_ENV = "META_MBA_TOKEN"
DEFAULT_TIMEOUT_S = 10.0
MAX_ATTEMPTS = 3
_BACKOFF_S = (0.5, 1.0)


class MbaApiError(Exception):
    """``kind`` ∈ {not_configured, rejected, unavailable, ambiguous}."""

    def __init__(self, kind: str, *, status: int | None = None, detail: str = "", attempts: int = 1) -> None:
        super().__init__(f"{kind}: {status or ''} {detail}".strip())
        self.kind = kind
        self.status = status
        self.detail = detail
        self.attempts = attempts


def mba_token() -> str:
    """Token de ``META_MBA_TOKEN`` leído en cada llamada (rotable sin rebuild)."""
    raw = os.environ.get(TOKEN_ENV, "")
    return "" if is_placeholder(raw) else raw.strip()


def _error_detail(resp: Any) -> str:
    """Dos formas de error conviven en la Cloud API de MBA: la del Graph API
    (``{"error": {"message"}}``, thread_control / agent_event) y el
    ``StandardError`` de los endpoints de configuración (``{"title",
    "detail"}``)."""
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            return err["message"]
        if isinstance(payload.get("detail"), str) and payload["detail"].strip():
            return payload["detail"].strip()
        if isinstance(payload.get("title"), str) and payload["title"].strip():
            return payload["title"].strip()
    text = resp.text if isinstance(resp.text, str) else ""
    return text[:300]


async def request_json(
    method: str,
    url: str,
    *,
    api_version: str,
    token: str,
    body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    error_cls: type[MbaApiError] = MbaApiError,
    retry_on_transport_error: bool = True,
) -> Any:
    """Una llamada a la Cloud API de MBA con la política de errores de arriba.

    Devuelve el JSON del 2xx tal cual (dict o lista; ``{}`` si el cuerpo viene
    vacío o no es JSON, p.ej. un 204). ``body=None`` manda la llamada sin
    cuerpo; ``{}`` manda un objeto vacío (onboarding sin catálogo).
    """
    if not token or is_placeholder(token):
        raise error_cls("not_configured", detail=f"{TOKEN_ENV} no configurado")
    import httpx  # import perezoso: el kit no arrastra vendors al importarse

    headers = {
        "Authorization": f"Bearer {token}",
        "X-API-Version": api_version,
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    last_status: int | None = None
    last_detail = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.request(method, url, json=body, params=params or None, headers=headers)
        except httpx.HTTPError as exc:
            last_status, last_detail = None, f"{type(exc).__name__}: {exc}"
            if not retry_on_transport_error:
                raise error_cls("ambiguous", detail=last_detail, attempts=attempt)
        else:
            if resp.status_code < 400:
                try:
                    payload = resp.json() if resp.content else {}
                except ValueError:
                    payload = {}
                return payload if isinstance(payload, (dict, list)) else {}
            detail = _error_detail(resp)
            if resp.status_code != 429 and resp.status_code < 500:
                raise error_cls("rejected", status=resp.status_code, detail=detail, attempts=attempt)
            last_status, last_detail = resp.status_code, detail
        if attempt < MAX_ATTEMPTS:
            await sleep(_BACKOFF_S[min(attempt, len(_BACKOFF_S)) - 1])
    raise error_cls("unavailable", status=last_status, detail=last_detail, attempts=MAX_ATTEMPTS)


async def post_json(
    url: str,
    body: dict[str, Any],
    *,
    api_version: str,
    token: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    error_cls: type[MbaApiError] = MbaApiError,
    retry_on_transport_error: bool = True,
) -> dict[str, Any]:
    """POST con cuerpo JSON; devuelve SIEMPRE un dict (``{}`` si Meta no mandó uno)."""
    payload = await request_json(
        "POST",
        url,
        api_version=api_version,
        token=token,
        body=body,
        timeout_s=timeout_s,
        sleep=sleep,
        error_cls=error_cls,
        retry_on_transport_error=retry_on_transport_error,
    )
    return payload if isinstance(payload, dict) else {}
