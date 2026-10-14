"""Adapter Medusa del puerto de reseñas (`/admin/product-reviews`)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.platform.medusa.client import MedusaAPIError
from src.platform.reviews.port import ProductReviewInput, ProductReviewsUnavailableError


def _unavailable(exc: MedusaAPIError) -> ProductReviewsUnavailableError:
    hint = (
        " — ¿está desplegado el módulo de reseñas en Medusa y corrida su migración?"
        if exc.status_code in (404, 500)
        else ""
    )
    return ProductReviewsUnavailableError(f"Medusa respondió {exc.status_code}{hint}")


def _iso_utc(ms: int) -> str:
    """ISO con zona (`Z`): Medusa rechaza una fecha sin zona (400)."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class MedusaProductReviews:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def upsert(self, review: ProductReviewInput) -> str:
        payload: dict[str, Any] = {
            "external_id": review.external_id,
            "product_id": review.product_id,
            "order_display_id": review.order_display_id,
            "rating": int(review.rating),
            "body": review.body,
            "author_name": review.author_name,
            "author_city": review.author_city,
            "photo_urls": list(review.photo_urls),
            "reply": review.reply,
            "status": review.status,
            "verified_purchase": True,
            "source": "whatsapp",
            "submitted_at": _iso_utc(review.submitted_at_ms),
        }
        # Cuerpos estrictos: lo que no hay no se manda (None ≠ «borrar»).
        payload = {k: v for k, v in payload.items() if v is not None}
        try:
            data = await self._client.upsert_product_review(payload)
        except MedusaAPIError as exc:
            raise _unavailable(exc) from exc
        return str(data["id"])

    async def upload_photo(self, filename: str, content: bytes, mime: str) -> str:
        try:
            return await self._client.upload_file(filename, content, mime)
        except MedusaAPIError as exc:
            raise _unavailable(exc) from exc
