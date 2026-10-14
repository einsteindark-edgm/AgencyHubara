"""API del plugin reviews — el panel «Reseñas» del dashboard (router DELGADO).

  GET  /reviews?status=          lista (más nuevas primero) + conteos por estado
  GET  /summary                  invitaciones (y por qué no salieron), reseñas,
                                 cupones, el modo, qué falta y la tienda
  POST /reviews/{id}/approve     publicar en la tienda (Medusa) → 502 si falla
  POST /reviews/{id}/hide        ocultar
  POST /reviews/{id}/reply       respuesta pública del equipo
  GET  /photos/{archivo}         la foto que subió el cliente (para moderarla)

La lógica vive en `domain/` (puro) y `agent/publishing.py`; acá solo HTTP. Las
dependencias son funciones de módulo para que los tests las reemplacen.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from src.plugins.reviews.agent.publishing import (
    StorefrontRebuild,
    hide_review,
    publish_review,
    reply_review,
)
from src.plugins.reviews.agent.settings import ReviewSettings
from src.plugins.reviews.agent.store import REVIEW_STATUSES, ReviewRecord, ReviewsStore
from src.plugins.reviews.domain.summary import summarize
from src.sdk.connectorkit import (
    ProductReviewsUnavailableError,
    get_coupon_grants_store,
    get_product_reviews_port,
)
from src.sdk.mediakit import is_safe_segment
from src.sdk.runtime import WORKSPACE_VAULT_DIR

router = APIRouter()
log = logging.getLogger(__name__)


# ── dependencias (reemplazables en tests) ──────────────────────────────────


def store() -> ReviewsStore:
    return ReviewsStore(WORKSPACE_VAULT_DIR)


def product_reviews() -> Any:
    return get_product_reviews_port()


def grants() -> Any:
    return get_coupon_grants_store()


def settings() -> ReviewSettings:
    return ReviewSettings.from_env()


def rebuild() -> StorefrontRebuild:
    return StorefrontRebuild(WORKSPACE_VAULT_DIR, url=os.getenv("STOREFRONT_DEPLOY_HOOK_URL"))


async def coupons_ready() -> bool:
    """¿GRACIAS5/GRACIAS10 existen, son personales y duran lo prometido? (lo
    mismo que exige el flujo antes de invitar)."""
    from src.plugins.reviews.agent.composition import coupons_ready as ready
    from src.sdk.connectorkit import get_promotions_port

    cfg = settings()
    return await ready(
        get_promotions_port(),
        (cfg.coupon_code_5, cfg.coupon_code_10),
        now_ms(),
        valid_for_ms=cfg.timing.give_up_after_ms + cfg.coupon_days * 86_400_000,
    )


#: Reconstrucciones programadas (referencia viva: asyncio no las retiene).
_PENDING_FLUSHES: set[asyncio.Task[Any]] = set()


async def _flush_later(rb: Any, delay_s: float) -> None:
    await asyncio.sleep(delay_s)
    try:
        await rb.flush(now_ms())
    except Exception as exc:  # noqa: BLE001 — la abre el próximo panel/moderación
        log.warning("storefront rebuild (pending) failed: %s", exc)


def _schedule_pending_rebuild(rb: Any) -> None:
    """Lo moderado dentro de la ventana de 10 min sale al vencer (premortem
    M3), aunque nadie vuelva a tocar el panel."""
    due = rb.due_at_ms()
    if due is None:
        return
    task = asyncio.create_task(_flush_later(rb, max(0.0, (due - now_ms()) / 1000)))
    _PENDING_FLUSHES.add(task)
    task.add_done_callback(_PENDING_FLUSHES.discard)


def now_ms() -> int:
    return int(time.time() * 1000)


# ── forma de salida ────────────────────────────────────────────────────────


def _review_json(record: ReviewRecord) -> dict[str, Any]:
    return {
        "review_id": record.review_id,
        "order_id": record.order_id,
        "display_id": record.display_id,
        "session_id": record.session_id,
        "rating": record.rating,
        "comment": record.comment,
        "publish_consent": record.publish_consent,
        "author_name": record.author_name,
        "author_city": record.author_city,
        "products": list(record.products),
        "photo_urls": [f"/api/reviews/photos/{p.split('/')[-1]}" for p in record.photo_files],
        "submitted_at_ms": record.submitted_at_ms,
        "status": record.status,
        "coupon": record.coupon or None,
        "reply": record.reply,
        "team_alerted": record.team_alerted,
        "published": (
            {"at_ms": record.published.get("at_ms"), "error": record.published.get("error")}
            if record.published
            else None
        ),
    }


def _found(record: ReviewRecord | None) -> ReviewRecord:
    if record is None:
        raise HTTPException(status_code=404, detail="Esa reseña no existe.")
    return record


# ── endpoints ──────────────────────────────────────────────────────────────


@router.get("/reviews")
def list_reviews(status: str | None = None) -> dict[str, Any]:
    if status is not None and status not in REVIEW_STATUSES:
        raise HTTPException(status_code=422, detail=f"Estado inválido: {status}")
    all_reviews = store().list_reviews()
    counts = {s: sum(1 for r in all_reviews if r.status == s) for s in REVIEW_STATUSES}
    shown = [r for r in all_reviews if status is None or r.status == status]
    return {"reviews": [_review_json(r) for r in shown], "counts": counts}


@router.get("/summary")
async def summary() -> dict[str, Any]:
    rb = rebuild()
    try:
        await rb.flush(now_ms())
    except Exception as exc:  # noqa: BLE001 — el panel abre igual
        log.warning("storefront rebuild (pending) failed: %s", exc)
    st = store()
    reviews = st.list_reviews()
    grants_store = grants()
    review_grants = []
    for session_id in {r.session_id for r in reviews}:
        try:
            review_grants.extend(g for g in grants_store.list_for(session_id) if g.source == "review")
        except ValueError:
            continue
    cfg = settings()
    try:
        ready = await coupons_ready()
    except Exception:  # noqa: BLE001 — Medusa caído: no listos
        ready = False
    return summarize(st.list_requests(), reviews, review_grants, st.list_plans()) | {
        "settings": {
            "mode": cfg.mode,
            "google_url": cfg.google_url,
            "flow_configured": cfg.flow_id is not None,
            "coupons_ready": ready,
        },
        "storefront": rb.status(),
    }


@router.post("/reviews/{review_id}/approve")
async def approve(review_id: str) -> dict[str, Any]:
    st = store()
    _found(st.get_review(review_id))
    rb = rebuild()
    try:
        record = await publish_review(st, product_reviews(), review_id, now_ms=now_ms(), rebuild=rb)
    except ProductReviewsUnavailableError as exc:
        raise HTTPException(
            status_code=502, detail=f"No se pudo publicar en la tienda: {exc}"
        ) from exc
    _schedule_pending_rebuild(rb)
    return {"review": _review_json(record)}


@router.post("/reviews/{review_id}/hide")
async def hide(review_id: str) -> dict[str, Any]:
    st = store()
    _found(st.get_review(review_id))
    try:
        record = await hide_review(st, product_reviews(), review_id, now_ms=now_ms(), rebuild=rebuild())
    except ProductReviewsUnavailableError as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo ocultar en la tienda: {exc}") from exc
    return {"review": _review_json(record)}


class ReplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: str = Field(max_length=600)


@router.post("/reviews/{review_id}/reply")
async def reply(review_id: str, body: ReplyBody) -> dict[str, Any]:
    st = store()
    _found(st.get_review(review_id))
    rb = rebuild()
    try:
        record = await reply_review(
            st, product_reviews(), review_id, body.reply, now_ms=now_ms(), rebuild=rb
        )
    except ProductReviewsUnavailableError as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo actualizar la tienda: {exc}") from exc
    _schedule_pending_rebuild(rb)
    return {"review": _review_json(record)}


@router.get("/photos/{filename}")
def photo(filename: str) -> FileResponse:
    if not is_safe_segment(filename):
        raise HTTPException(status_code=400, detail="Nombre de archivo inválido.")
    path = store().root / "photos" / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Esa foto no existe.")
    return FileResponse(path)
