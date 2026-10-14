"""API del plugin reviews — el panel «Reseñas» del dashboard (router DELGADO).

  GET  /reviews?status=          lista (más nuevas primero) + conteos por estado
  GET  /summary                  invitaciones, reseñas, cupones y el modo
  POST /reviews/{id}/approve     publicar en la tienda (Medusa) → 502 si falla
  POST /reviews/{id}/hide        ocultar
  POST /reviews/{id}/reply       respuesta pública del equipo
  GET  /photos/{archivo}         la foto que subió el cliente (para moderarla)

La lógica vive en `domain/` (puro) y `agent/publishing.py`; acá solo HTTP. Las
dependencias son funciones de módulo para que los tests las reemplacen.
"""
from __future__ import annotations

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
def summary() -> dict[str, Any]:
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
    return summarize(st.list_requests(), reviews, review_grants) | {
        "settings": {"mode": cfg.mode, "google_url": cfg.google_url}
    }


@router.post("/reviews/{review_id}/approve")
async def approve(review_id: str) -> dict[str, Any]:
    st = store()
    _found(st.get_review(review_id))
    try:
        record = await publish_review(st, product_reviews(), review_id, now_ms=now_ms(), rebuild=rebuild())
    except ProductReviewsUnavailableError as exc:
        raise HTTPException(
            status_code=502, detail=f"No se pudo publicar en la tienda: {exc}"
        ) from exc
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
    try:
        record = await reply_review(
            st, product_reviews(), review_id, body.reply, now_ms=now_ms(), rebuild=rebuild()
        )
    except ProductReviewsUnavailableError as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo actualizar la tienda: {exc}") from exc
    return {"review": _review_json(record)}


@router.get("/photos/{filename}")
def photo(filename: str) -> FileResponse:
    if not is_safe_segment(filename):
        raise HTTPException(status_code=400, detail="Nombre de archivo inválido.")
    path = store().root / "photos" / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Esa foto no existe.")
    return FileResponse(path)
