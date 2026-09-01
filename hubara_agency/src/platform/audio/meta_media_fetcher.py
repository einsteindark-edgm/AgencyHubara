"""Descarga de media_id de WhatsApp Cloud API.

Flujo de 2 pasos según Meta:

  1. `GET /v23.0/{media_id}` → devuelve `{"url": "https://lookaside.fbsbx.com/..."}`
  2. `GET <url>` con el access_token en Bearer → devuelve los bytes

El URL del paso 2 expira a los 5 minutos. La activity de transcripción
debe llamarse pronto tras el webhook inbound.

PREMORTEM #4: retry con backoff exponencial. Sin retry, un 5xx transitorio
de Meta perdía el audio del cliente para siempre. Ahora reintentamos hasta
3 veces con base 0.7s (totales ~4.5s worst case), abajo del timeout de la
activity (60s) — deja margen para el provider STT.
"""
from __future__ import annotations

import asyncio

import httpx
import structlog

from src.platform.config import WHATSAPP_ACCESS_TOKEN

logger = structlog.get_logger()

GRAPH_API_VERSION = "v23.0"

_MAX_ATTEMPTS = 3
_BASE_DELAY_S = 0.7


async def fetch_media_metadata(media_id: str) -> dict | None:
    """Step 1: resolver media_id → URL temporal. Retry en 5xx/transitorios."""
    if not WHATSAPP_ACCESS_TOKEN:
        logger.warning("fetch_media_metadata: no token")
        return None
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{media_id}"
    headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(url, headers=headers)
        except httpx.HTTPError as e:
            if attempt == _MAX_ATTEMPTS:
                logger.warning(
                    "fetch_media_metadata.transport_failed",
                    media_id=media_id,
                    error=str(e),
                    attempts=attempt,
                )
                return None
            await asyncio.sleep(_BASE_DELAY_S * (2 ** (attempt - 1)))
            continue

        # Retry sólo en 5xx + 429 (transitorios). 4xx (excepto 429) = config
        # error, no reintentar.
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code >= 500 or resp.status_code == 429:
            if attempt == _MAX_ATTEMPTS:
                logger.warning(
                    "fetch_media_metadata.bad_status_after_retries",
                    media_id=media_id,
                    status=resp.status_code,
                    body=resp.text[:300],
                )
                return None
            await asyncio.sleep(_BASE_DELAY_S * (2 ** (attempt - 1)))
            continue
        # 4xx no recuperable
        logger.warning(
            "fetch_media_metadata.client_error",
            media_id=media_id,
            status=resp.status_code,
            body=resp.text[:300],
        )
        return None
    return None


async def fetch_media_bytes(
    media_id: str, *, max_bytes: int | None = None
) -> tuple[bytes, str] | None:
    """Resuelve metadata + descarga binarios. Devuelve `(bytes, mime_type)`
    o None si falla. mime_type viene del metadata response.

    Retry interno en metadata (paso 1) y en download (paso 2).

    ``max_bytes``: cap de descarga (documentos inbound — WhatsApp permite
    hasta 100 MB y sin cap un cliente infla la RAM del API y el vault). El
    corte usa el ``file_size`` declarado del metadata ANTES de bajar los
    bytes; el chequeo post-download cubre un ``file_size`` ausente o
    mentiroso. Sin ``max_bytes`` (audio/visión) no hay límite — comportamiento
    histórico.
    """
    meta = await fetch_media_metadata(media_id)
    if not meta:
        return None
    url = meta.get("url")
    mime_type = meta.get("mime_type", "application/octet-stream")
    file_size = meta.get("file_size")
    if not url:
        logger.warning("fetch_media_bytes: no url in metadata", media_id=media_id)
        return None
    if (
        max_bytes is not None
        and isinstance(file_size, (int, float))
        and file_size > max_bytes
    ):
        logger.warning(
            "fetch_media_bytes.skipped_declared_too_large",
            media_id=media_id,
            declared_size=file_size,
            max_bytes=max_bytes,
        )
        return None
    headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=headers)
        except httpx.HTTPError as e:
            if attempt == _MAX_ATTEMPTS:
                logger.warning(
                    "fetch_media_bytes.transport_failed",
                    media_id=media_id,
                    error=str(e),
                )
                return None
            await asyncio.sleep(_BASE_DELAY_S * (2 ** (attempt - 1)))
            continue

        if resp.status_code == 200:
            if max_bytes is not None and len(resp.content) > max_bytes:
                logger.warning(
                    "fetch_media_bytes.dropped_body_too_large",
                    media_id=media_id,
                    bytes=len(resp.content),
                    declared_size=file_size,
                    max_bytes=max_bytes,
                )
                return None
            logger.info(
                "fetch_media_bytes.ok",
                media_id=media_id,
                bytes=len(resp.content),
                declared_size=file_size,
                mime_type=mime_type,
                attempts=attempt,
            )
            return (resp.content, mime_type)
        if resp.status_code >= 500 or resp.status_code == 429:
            if attempt == _MAX_ATTEMPTS:
                logger.warning(
                    "fetch_media_bytes.bad_status_after_retries",
                    media_id=media_id,
                    status=resp.status_code,
                )
                return None
            await asyncio.sleep(_BASE_DELAY_S * (2 ** (attempt - 1)))
            continue
        # 4xx no recuperable (típicamente URL expirada > 5min)
        logger.warning(
            "fetch_media_bytes.client_error_or_url_expired",
            media_id=media_id,
            status=resp.status_code,
        )
        return None
    return None
