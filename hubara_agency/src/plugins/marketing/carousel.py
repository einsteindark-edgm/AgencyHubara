"""Tarjetas del carrusel de una campaña — el ÚNICO I/O del carrusel.

Resuelve cada handle en el catálogo, baja la foto del producto (Medusa/CDN)
y la sube a Meta (`upload_media`, media_id válido ~30 días). El media_id se
cachea en la campaña (`carousel_media`) para que el envío de prueba y el
envío real (y sus retries) no re-suban la misma foto. La decisión de qué va
en cada tarjeta es del dominio puro (`build_carousel_cards`).
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from src.plugins.marketing.campaign_store import CampaignStore
from src.plugins.marketing.domain.campaigns import (
    build_carousel_cards,
    carousel_handles,
)
from src.sdk.catalogkit import ProductNotFoundError
from src.sdk.connectorkit import get_catalog_client
from src.sdk.mediakit import upload_media
from src.sdk.messagingkit import CarouselCard
from src.sdk.runtime import WORKSPACE_VAULT_DIR

#: Meta conserva un media_id ~30 días; renovamos con margen.
MEDIA_TTL_MS = 25 * 24 * 60 * 60 * 1000
_FETCH_TIMEOUT_S = 20
_MAX_IMAGE_BYTES = 5 * 1024 * 1024


class CarouselError(RuntimeError):
    """El carrusel no se puede armar (producto sin foto, catálogo, Meta)."""


async def fetch_image_bytes(url: str) -> tuple[bytes, str]:
    """Baja la foto del producto. Devuelve (bytes, mime)."""
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_S, follow_redirects=True) as client:
        response = await client.get(url)
    response.raise_for_status()
    content = response.content
    if len(content) > _MAX_IMAGE_BYTES:
        raise CarouselError(f"foto demasiado grande ({len(content)} bytes): {url}")
    mime = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
    if mime not in {"image/jpeg", "image/png", "image/webp"}:
        # CDNs a veces devuelven octet-stream: inferir por la extensión.
        lower = url.lower().split("?")[0]
        mime = "image/png" if lower.endswith(".png") else "image/jpeg"
    return content, mime


def _phone_number_id() -> str:
    phone = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    if not phone:
        raise CarouselError("WHATSAPP_PHONE_NUMBER_ID no configurado")
    return phone


async def resolve_campaign_carousel(
    campaign: dict[str, Any], *, now_ms: int
) -> list[CarouselCard]:
    """Tarjetas listas para enviar. Sube (o reutiliza) la foto de cada
    producto y persiste el cache en la campaña. `[]` si no hay carrusel."""
    handles = carousel_handles(campaign)
    if not handles:
        return []
    catalog = get_catalog_client()
    products: dict[str, Any] = {}
    for handle in handles:
        try:
            products[handle] = await catalog.get_by_handle(handle)
        except ProductNotFoundError as e:
            raise CarouselError(f"producto {handle!r} ya no está en el catálogo") from e

    cache: dict[str, Any] = dict(campaign.get("carousel_media") or {})
    media_ids: dict[str, str] = {}
    changed = False
    for handle in handles:
        entry = cache.get(handle) if isinstance(cache.get(handle), dict) else None
        uploaded_at = entry.get("uploaded_at_ms") if entry else None
        if (
            entry
            and entry.get("media_id")
            and isinstance(uploaded_at, int)
            and now_ms - uploaded_at < MEDIA_TTL_MS
        ):
            media_ids[handle] = str(entry["media_id"])
            continue
        product = products[handle]
        url = getattr(product, "thumbnail", None) or _first_image_url(product)
        if not url:
            raise CarouselError(
                f"producto {handle!r} sin foto en el catálogo — cargale una en Medusa"
            )
        content, mime = await fetch_image_bytes(url)
        media_id = await upload_media(_phone_number_id(), content, mime)
        media_ids[handle] = media_id
        cache[handle] = {"media_id": media_id, "uploaded_at_ms": now_ms, "url": url}
        changed = True

    if changed:
        campaign["carousel_media"] = cache
        store = CampaignStore(WORKSPACE_VAULT_DIR)
        fresh = store.get(campaign["id"]) or campaign
        fresh["carousel_media"] = cache
        store.save(fresh)

    try:
        return build_carousel_cards(handles, products, media_ids=media_ids)
    except ValueError as e:
        raise CarouselError(str(e)) from e


def _first_image_url(product: Any) -> str | None:
    images = getattr(product, "images", None) or []
    first = images[0] if images else None
    return getattr(first, "url", None) if first is not None else None


__all__ = [
    "MEDIA_TTL_MS",
    "CarouselError",
    "fetch_image_bytes",
    "resolve_campaign_carousel",
]
