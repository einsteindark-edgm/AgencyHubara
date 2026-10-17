"""Composición del puerto de reseñas de producto."""
from __future__ import annotations

import logging

from src.platform.medusa.composition import get_medusa_client, get_medusa_settings
from src.platform.reviews.medusa import MedusaProductReviews
from src.platform.reviews.port import (
    ProductReviewInput,
    ProductReviewsPort,
    ProductReviewsUnavailableError,
)

log = logging.getLogger(__name__)


class UnavailableProductReviews:
    """Sin Medusa no hay dónde publicar: cada operación lo dice (la API del
    plugin responde 502 con el motivo), nunca un éxito falso."""

    async def upsert(self, review: ProductReviewInput) -> str:
        raise ProductReviewsUnavailableError("Medusa no está configurado en este proceso")

    async def upload_photo(self, filename: str, content: bytes, mime: str) -> str:
        raise ProductReviewsUnavailableError("Medusa no está configurado en este proceso")


def get_product_reviews_port() -> ProductReviewsPort:
    try:
        settings = get_medusa_settings()
    except Exception as exc:  # noqa: BLE001 — sin MEDUSA_* no se publica
        log.warning("ProductReviewsPort = Unavailable (Medusa sin configurar: %s)", exc)
        return UnavailableProductReviews()
    if not getattr(settings, "base_url", None):
        return UnavailableProductReviews()
    return MedusaProductReviews(get_medusa_client())


__all__ = ["UnavailableProductReviews", "get_product_reviews_port"]
