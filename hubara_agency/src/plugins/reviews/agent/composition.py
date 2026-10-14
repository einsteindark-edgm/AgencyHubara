"""Dependencias del agente de reseñas (composition root del plugin).

`ReviewsDeps` es lo que reciben `plan_step` / `send_request` /
`process_submission`: los tests pasan dobles, el worker las reales
(`get_reviews_deps`). Todo I/O externo entra por el SDK (P-28/P-31): pedidos
y cupones por connectorkit, WhatsApp por messagingkit/mediakit, el vault por
runtime y el paso a humano por el cast a chats.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ReviewsDeps:
    settings: Any
    store: Any
    metadata: Any
    order_facts: Any
    order_query: Any
    grants: Any
    upload_media: Any
    fetch_media: Any
    fetch_url: Any
    send_flow: Any
    send_template: Any
    send_cta: Any
    escalate: Any
    quiet_hours_end: Any
    vault_dir: Path
    #: `() → bool`: GRACIAS5/GRACIAS10 existen, rigen y son «solo quien lo
    #: recibió» (revisión M5: sin eso no se invita).
    coupons_ready: Any = None


async def _fetch_url(url: str) -> bytes | None:
    """La foto del producto (Medusa/R2) para la tarjeta; None si no se puede."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200 or len(resp.content) > 8 * 1024 * 1024:
        return None
    return resp.content


def _quiet_hours_end(session_id: str, now_ms: int) -> int | None:
    """Fin del horario de silencio del cliente (hora local), o None si ya se
    puede escribir. Avanza de a 15 min (máximo 14 h)."""
    from datetime import datetime, timezone

    from src.sdk.messagingkit import is_quiet_hours_for_session

    def _quiet(ms: int) -> bool:
        return is_quiet_hours_for_session(session_id, datetime.fromtimestamp(ms / 1000, tz=timezone.utc))

    if not _quiet(now_ms):
        return None
    step = 15 * 60_000
    probe = now_ms
    for _ in range(14 * 4):
        probe += step
        if not _quiet(probe):
            return probe
    return probe


async def coupons_ready(promotions: Any, codes: tuple[str, ...], now_ms: int) -> bool:
    """¿Cada código existe, rige hoy y es personal (`granted_only`)? Un cupón
    público con ese código sería un descuento para cualquiera."""
    from src.sdk.connectorkit import coupon_problem

    for code in codes:
        try:
            promotion = await promotions.get_by_code(code)
        except Exception:  # noqa: BLE001 — Medusa caído: aún no
            return False
        if promotion is None or not getattr(promotion, "granted_only", False):
            return False
        if coupon_problem(promotion, now_ms=now_ms) is not None:
            return False
    return True


def get_reviews_deps() -> ReviewsDeps:
    """Las dependencias reales (worker). Se resuelven en cada llamada a la
    activity (R-STATELESS: el vault y los puertos los cachea su composición)."""
    import time

    from src.plugins.reviews.agent.settings import ReviewSettings
    from src.plugins.reviews.agent.store import ReviewsStore
    from src.plugins.reviews.api.chats_cast import escalate_low_rating
    from src.sdk.connectorkit import (
        get_coupon_grants_store,
        get_order_facts_port,
        get_order_query_port,
        get_promotions_port,
    )
    from src.sdk.mediakit import fetch_media_bytes, upload_media
    from src.sdk.messagingkit import (
        send_cta_url_to_session,
        send_flow_to_session,
        send_template_to_session,
    )
    from src.sdk.runtime import WORKSPACE_VAULT_DIR, FilesystemMetadataStore

    async def _fetch_media(media_id: str):
        return await fetch_media_bytes(media_id, max_bytes=8 * 1024 * 1024)

    vault = Path(WORKSPACE_VAULT_DIR)
    settings = ReviewSettings.from_env()

    async def _coupons_ready() -> bool:
        return await coupons_ready(
            get_promotions_port(),
            (settings.coupon_code_5, settings.coupon_code_10),
            int(time.time() * 1000),
        )

    return ReviewsDeps(
        settings=settings,
        store=ReviewsStore(vault),
        metadata=FilesystemMetadataStore(vault),
        order_facts=get_order_facts_port(),
        order_query=get_order_query_port(),
        grants=get_coupon_grants_store(),
        upload_media=upload_media,
        fetch_media=_fetch_media,
        fetch_url=_fetch_url,
        send_flow=send_flow_to_session,
        send_template=send_template_to_session,
        send_cta=send_cta_url_to_session,
        escalate=escalate_low_rating,
        quiet_hours_end=_quiet_hours_end,
        vault_dir=vault,
        coupons_ready=_coupons_ready,
    )
