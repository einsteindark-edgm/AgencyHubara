"""Moderar y publicar reseñas (lo que hace el equipo desde el dashboard).

D6 del operador (2026-10-13): se publican todas las que tengan permiso y no
sean ofensivas — también las de pocas estrellas (publicar solo las buenas daría
una idea falsa) — siempre con aprobación manual. Sin la casilla de permiso
(Ley 1581) la reseña cuenta para el promedio pero no se muestra: solo
estrellas, anónima, sin texto, ciudad ni foto.

La reseña es por pedido; en Medusa va una fila por producto del pedido
(`external_id = <review_id>:<product_id>`, upsert idempotente). Al publicar,
ocultar o responder se pide reconstruir la tienda (fichas estáticas), como
mucho una vez cada `every_ms`.
"""
from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from src.plugins.reviews.agent.store import ReviewRecord, ReviewsStore
from src.sdk.connectorkit import ProductReviewInput, ProductReviewsUnavailableError
from src.sdk.runtime import atomic_write_json, is_placeholder

_ANONYMOUS = "Cliente de Hubara"
_MIMES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}

Rebuild = Callable[[int], Awaitable[None]]


class StorefrontRebuild:
    """Pide a Cloudflare Pages reconstruir hubara.com.co (deploy hook), como
    mucho una vez cada `every_ms` (`_reviews/storefront_rebuild.json`). Sin
    hook configurado no hace nada: la reseña igual queda en Medusa y sale en
    la próxima reconstrucción."""

    def __init__(
        self,
        vault_dir: Path,
        *,
        url: str | None,
        post: Callable[[str], Awaitable[Any]] | None = None,
        every_ms: int = 600_000,
    ) -> None:
        self._marker = Path(vault_dir) / "_reviews" / "storefront_rebuild.json"
        self._url = (url or "").strip()
        self._post = post or _post_hook
        self._every_ms = every_ms

    async def __call__(self, now_ms: int) -> None:
        if is_placeholder(self._url):
            return
        try:
            last = int(json.loads(self._marker.read_text(encoding="utf-8")).get("last_at_ms", 0))
        except (OSError, ValueError, AttributeError):
            last = 0
        if now_ms - last < self._every_ms:
            return
        self._marker.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._marker, {"last_at_ms": now_ms})
        await self._post(self._url)


async def _post_hook(url: str) -> None:
    import httpx

    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(url)


def _display_number(display_id: str) -> int | None:
    digits = re.sub(r"\D", "", display_id or "")
    return int(digits) if digits else None


async def _photo_urls(store: ReviewsStore, port: Any, record: ReviewRecord) -> tuple[str, ...]:
    if not record.publish_consent:
        return ()
    already = tuple(record.published.get("photo_urls") or ())
    if already:
        return already
    urls = []
    for rel in record.photo_files:
        path = store.root / rel
        try:
            content = path.read_bytes()
        except OSError:
            continue
        urls.append(await port.upload_photo(path.name, content, _MIMES.get(path.suffix.lower(), "image/jpeg")))
    return tuple(urls)


async def _push(store: ReviewsStore, port: Any, record: ReviewRecord, status: str, photo_urls: tuple[str, ...]) -> list[str]:
    ids = []
    consent = record.publish_consent
    for product in record.products:
        product_id = product.get("product_id")
        if not product_id:
            continue
        ids.append(
            await port.upsert(
                ProductReviewInput(
                    external_id=f"{record.review_id}:{product_id}",
                    product_id=str(product_id),
                    order_display_id=_display_number(record.display_id),
                    rating=record.rating,
                    body=record.comment if consent else None,
                    author_name=record.author_name if consent else _ANONYMOUS,
                    author_city=record.author_city if consent else None,
                    photo_urls=photo_urls,
                    reply=record.reply,
                    status=status,
                    submitted_at_ms=record.submitted_at_ms,
                )
            )
        )
    return ids


def _get(store: ReviewsStore, review_id: str) -> ReviewRecord:
    record = store.get_review(review_id)
    if record is None:
        raise KeyError(review_id)
    return record


async def publish_review(
    store: ReviewsStore, port: Any, review_id: str, *, now_ms: int, rebuild: Rebuild
) -> ReviewRecord:
    """Aprobar: la reseña sale en la tienda. Si Medusa falla, queda pendiente
    con el motivo (el dashboard lo muestra) y se relanza el error."""
    record = _get(store, review_id)
    try:
        photo_urls = await _photo_urls(store, port, record)
        ids = await _push(store, port, record, "approved", photo_urls)
    except ProductReviewsUnavailableError as exc:
        store.update_review(review_id, published={**record.published, "error": str(exc), "at_ms": now_ms})
        raise
    updated = store.update_review(
        review_id,
        status="approved",
        published={"at_ms": now_ms, "ids": ids, "photo_urls": list(photo_urls), "error": None},
    )
    await rebuild(now_ms)
    assert updated is not None
    return updated


async def hide_review(
    store: ReviewsStore, port: Any, review_id: str, *, now_ms: int, rebuild: Rebuild
) -> ReviewRecord:
    """Ocultar: deja de verse en la tienda (si estaba publicada) y en la lista."""
    record = _get(store, review_id)
    if record.published.get("ids"):
        await _push(store, port, record, "hidden", tuple(record.published.get("photo_urls") or ()))
        await rebuild(now_ms)
    updated = store.update_review(review_id, status="hidden")
    assert updated is not None
    return updated


async def reply_review(
    store: ReviewsStore, port: Any, review_id: str, reply: str, *, now_ms: int, rebuild: Rebuild
) -> ReviewRecord:
    """La respuesta pública del equipo (sale debajo de la reseña publicada)."""
    record = store.update_review(review_id, reply=(reply or "").strip()[:600] or None)
    if record is None:
        raise KeyError(review_id)
    if record.status == "approved" and record.published.get("ids"):
        await _push(store, port, record, "approved", tuple(record.published.get("photo_urls") or ()))
        await rebuild(now_ms)
    return record
