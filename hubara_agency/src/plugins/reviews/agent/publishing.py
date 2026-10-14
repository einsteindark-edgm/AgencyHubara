"""Moderar y publicar reseñas (lo que hace el equipo desde el dashboard).

D6 del operador (2026-10-13): se publican todas las que tengan permiso y no
sean ofensivas — también las de pocas estrellas (publicar solo las buenas daría
una idea falsa) — siempre con aprobación manual. Sin la casilla de permiso
(Ley 1581) la reseña cuenta para el promedio pero no se muestra: solo
estrellas, anónima, sin texto, ciudad ni foto.

La reseña es por pedido; en Medusa va una fila por producto del pedido
(`external_id = <clave pública>:<product_id>`, upsert idempotente; la clave es
un hash: el id del pedido de Medusa abre sus datos personales). Al publicar o
responder se pide reconstruir la tienda (fichas estáticas) como mucho una vez
cada `every_ms`, y lo que cae dentro queda pendiente; al ocultar, de una.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from src.plugins.reviews.agent.store import ReviewRecord, ReviewsStore
from src.sdk.connectorkit import ProductReviewInput, ProductReviewsUnavailableError
from src.sdk.runtime import atomic_write_json, is_placeholder

_ANONYMOUS = "Cliente de Hubara"
_MIMES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}

Rebuild = Callable[..., Awaitable[Any]]

log = logging.getLogger(__name__)


async def _rebuild_best_effort(rebuild: Rebuild, now_ms: int, *, urgent: bool = False) -> None:
    """La reseña ya está en Medusa: un hook caído no es una publicación fallida
    (sale en la próxima reconstrucción)."""
    try:
        if urgent:
            await rebuild(now_ms, urgent=True)
        else:
            await rebuild(now_ms)
    except Exception as exc:  # noqa: BLE001 — revisión L5
        log.warning("storefront rebuild failed: %s", exc)


class StorefrontRebuild:
    """Pide a Cloudflare Pages reconstruir hubara.com.co (deploy hook), como
    mucho una vez cada `every_ms` (`_reviews/storefront_rebuild.json`).

    Lo que cae dentro de esa ventana queda PENDIENTE (`due_at_ms`) y sale con
    `flush` al vencer — antes se perdía y la tienda seguía mostrando lo viejo
    (premortem M3). `urgent` (ocultar) no espera. Sin hook configurado no hace
    nada (lo avisa en el log): la reseña igual queda en Medusa."""

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

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self._marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, **fields: Any) -> None:
        self._marker.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._marker, {**self._read(), **fields})

    def status(self) -> dict[str, Any]:
        """Para el panel: última reconstrucción pedida bien, último error y si
        hay una pendiente."""
        data = self._read()
        return {
            "configured": not is_placeholder(self._url),
            "last_ok_at_ms": data.get("last_at_ms"),
            "last_error": data.get("last_error"),
            "due_at_ms": data.get("due_at_ms"),
        }

    def due_at_ms(self) -> int | None:
        due = self._read().get("due_at_ms")
        return due if isinstance(due, int) else None

    async def __call__(self, now_ms: int, *, urgent: bool = False) -> bool:
        """True si pidió la reconstrucción ahora; False si quedó pendiente."""
        if is_placeholder(self._url):
            log.warning("STOREFRONT_DEPLOY_HOOK_URL sin configurar: la tienda no se reconstruye sola")
            return False
        last = self._read().get("last_at_ms")
        last = last if isinstance(last, int) else 0
        if not urgent and now_ms - last < self._every_ms:
            if self.due_at_ms() is None:
                self._write(due_at_ms=last + self._every_ms)
            return False
        await self._fire(now_ms)
        return True

    async def flush(self, now_ms: int) -> bool:
        """Pide la reconstrucción pendiente si ya venció. True si la pidió."""
        due = self.due_at_ms()
        if due is None or now_ms < due or is_placeholder(self._url):
            return False
        await self._fire(now_ms)
        return True

    async def _fire(self, now_ms: int) -> None:
        try:
            await self._post(self._url)
        except Exception as exc:
            # Solo un llamado que salió bien cuenta para el espaciado.
            self._write(last_error=f"{type(exc).__name__}: {exc}"[:300], last_error_at_ms=now_ms)
            raise
        self._write(last_at_ms=now_ms, due_at_ms=None, last_error=None)


async def post_hook(url: str, *, transport: Any = None) -> None:
    """POST al deploy hook; un 4xx/5xx (hook revocado) es un error."""
    import httpx

    async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
        response = await client.post(url)
        response.raise_for_status()


_post_hook = post_hook


def public_review_key(review_id: str) -> str:
    """La clave de la reseña que ve Medusa (y cualquiera en la tienda): un hash,
    nunca el id del pedido (con él se leen sus datos — premortem M1)."""
    return "rv_" + hashlib.sha256(f"hubara-review:{review_id}".encode()).hexdigest()[:20]


def _display_number(display_id: str) -> int | None:
    digits = re.sub(r"\D", "", display_id or "")
    return int(digits) if digits else None


async def _photo_urls(store: ReviewsStore, port: Any, record: ReviewRecord) -> tuple[str, ...]:
    """Sube las fotos (con permiso) una sola vez: las URLs quedan guardadas
    apenas suben, así un reintento no deja objetos públicos huérfanos (M9)."""
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
    if urls:
        store.update_review(record.review_id, published={**record.published, "photo_urls": urls})
    return tuple(urls)


async def _push(store: ReviewsStore, port: Any, record: ReviewRecord, status: str, photo_urls: tuple[str, ...]) -> list[str]:
    ids = []
    consent = record.publish_consent
    key = public_review_key(record.review_id)
    for product in record.products:
        product_id = product.get("product_id")
        if not product_id:
            continue
        ids.append(
            await port.upsert(
                ProductReviewInput(
                    external_id=f"{key}:{product_id}",
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
    if not any(p.get("product_id") for p in record.products):
        exc = ProductReviewsUnavailableError("el pedido no tiene productos publicables (¿se borraron en Medusa?)")
        store.update_review(review_id, published={**record.published, "error": str(exc), "at_ms": now_ms})
        raise exc
    try:
        photo_urls = await _photo_urls(store, port, record)
        ids = await _push(store, port, record, "approved", photo_urls)
    except ProductReviewsUnavailableError as exc:
        fresh = _get(store, review_id)
        store.update_review(review_id, published={**fresh.published, "error": str(exc), "at_ms": now_ms})
        raise
    updated = store.update_review(
        review_id,
        status="approved",
        published={"at_ms": now_ms, "ids": ids, "photo_urls": list(photo_urls), "error": None},
    )
    await _rebuild_best_effort(rebuild, now_ms)
    assert updated is not None
    return updated


async def hide_review(
    store: ReviewsStore, port: Any, review_id: str, *, now_ms: int, rebuild: Rebuild
) -> ReviewRecord:
    """Ocultar: deja de verse en la tienda y en la lista. Si alguna vez se
    intentó publicar (aunque haya quedado a medias) se baja cada fila, y la
    tienda se reconstruye de una (datos personales, foto equivocada — M5/W4)."""
    record = _get(store, review_id)
    if record.published:
        await _push(store, port, record, "hidden", tuple(record.published.get("photo_urls") or ()))
        await _rebuild_best_effort(rebuild, now_ms, urgent=True)
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
        await _rebuild_best_effort(rebuild, now_ms)
    return record
