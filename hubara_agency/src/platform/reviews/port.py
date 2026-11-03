"""Puerto de reseñas de producto (Medusa `productReview`, 2026-10-13).

El plugin `reviews` publica en la tienda la reseña que el equipo aprueba: una
fila por producto del pedido. El contrato del módulo vive en el backend de
Medusa (HubaraAdministrador/hubara_backend, `src/modules/product-review`):
upsert por `external_id`, cuerpos estrictos (campos desconocidos = 400) y
fecha ISO con zona.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

#: Estados de una reseña en Medusa (la tienda solo muestra `approved`).
PRODUCT_REVIEW_STATUSES = ("pending", "approved", "hidden")


@dataclass(frozen=True)
class ProductReviewInput:
    """Una reseña para UN producto (la calificación es por pedido)."""

    #: `<id de la reseña en Hubara>:<product_id>` — idempotencia del upsert.
    external_id: str
    product_id: str
    order_display_id: int | None
    rating: int
    body: str | None
    author_name: str
    author_city: str | None
    photo_urls: tuple[str, ...]
    reply: str | None
    status: str
    submitted_at_ms: int


class ProductReviewsUnavailableError(RuntimeError):
    """Medusa no respondió o rechazó la reseña (el módulo aún no desplegado,
    la migración sin correr, un 4xx). El texto es para el operador."""


@runtime_checkable
class ProductReviewsPort(Protocol):
    async def upsert(self, review: ProductReviewInput) -> str: ...
    async def upload_photo(self, filename: str, content: bytes, mime: str) -> str: ...


class FakeProductReviews:
    """Doble oficial para tests (P-27): mismo contrato de upsert."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self._ids: dict[str, str] = {}
        self.uploads: list[str] = []

    async def upsert(self, review: ProductReviewInput) -> str:
        medusa_id = self._ids.setdefault(review.external_id, f"prev_{len(self._ids) + 1}")
        self.rows[medusa_id] = {**self.rows.get(medusa_id, {}), **review.__dict__}
        return medusa_id

    async def upload_photo(self, filename: str, content: bytes, mime: str) -> str:
        self.uploads.append(filename)
        return f"https://fake.cdn/{filename}"


__all__ = [
    "FakeProductReviews",
    "PRODUCT_REVIEW_STATUSES",
    "ProductReviewInput",
    "ProductReviewsPort",
    "ProductReviewsUnavailableError",
]
