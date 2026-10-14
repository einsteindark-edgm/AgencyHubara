"""El puerto de reseñas de producto (Medusa `productReview`, 2026-10-13).

Hubara publica en Medusa la reseña que el equipo aprueba, una fila por
producto del pedido, por la API admin del módulo `productReview`:
  * `POST /admin/product-reviews` hace upsert por `external_id` (repetir no
    duplica); la fecha va en ISO con zona (Medusa rechaza una sin zona);
  * las fotos se suben al almacenamiento de Medusa (`POST /admin/uploads`) y
    la reseña guarda sus URLs.
El doble oficial (`FakeProductReviews`) cumple el mismo contrato.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from src.platform.medusa.client import HttpMedusaClient
from src.platform.reviews.medusa import MedusaProductReviews
from src.platform.reviews.port import FakeProductReviews, ProductReviewInput

_REVIEW = ProductReviewInput(
    external_id="rev_order_01J:prod_1",
    product_id="prod_1",
    order_display_id=31,
    rating=5,
    body="Hermosa",
    author_name="Ana G.",
    author_city="Bogotá",
    photo_urls=("https://cdn.test/r/1.jpg",),
    reply=None,
    status="approved",
    submitted_at_ms=1_791_000_000_000,
)


@pytest.mark.asyncio
async def test_upsert_posts_the_contract_body_with_an_aware_timestamp():
    client = HttpMedusaClient(base_url="https://m.test", admin_token="sk_x")
    with respx.mock(base_url="https://m.test") as r:
        route = r.post("/admin/product-reviews").mock(
            return_value=httpx.Response(200, json={"product_review": {"id": "prev_1"}})
        )
        medusa_id = await MedusaProductReviews(client).upsert(_REVIEW)
        body = json.loads(route.calls[0].request.content)
    await client.aclose()

    assert medusa_id == "prev_1"
    submitted_at = body.pop("submitted_at")
    assert body == {
        "external_id": "rev_order_01J:prod_1",
        "product_id": "prod_1",
        "order_display_id": 31,
        "rating": 5,
        "body": "Hermosa",
        "author_name": "Ana G.",
        "author_city": "Bogotá",
        "photo_urls": ["https://cdn.test/r/1.jpg"],
        "status": "approved",
        "verified_purchase": True,
        "source": "whatsapp",
    }
    assert submitted_at == "2026-10-03T04:00:00Z"
    assert "reply" not in body  # None no se manda (Medusa es estricto)


@pytest.mark.asyncio
async def test_upload_photo_returns_the_stored_url():
    client = HttpMedusaClient(base_url="https://m.test", admin_token="sk_x")
    with respx.mock(base_url="https://m.test") as r:
        route = r.post("/admin/uploads").mock(
            return_value=httpx.Response(200, json={"files": [{"id": "f1", "url": "https://cdn.test/r/1.jpg"}]})
        )
        url = await MedusaProductReviews(client).upload_photo("order_01J_0.jpg", b"\xff\xd8x", "image/jpeg")
        request = route.calls[0].request
    await client.aclose()

    assert url == "https://cdn.test/r/1.jpg"
    assert b'name="files"; filename="order_01J_0.jpg"' in request.content
    assert request.headers["authorization"].startswith("Basic ")


@pytest.mark.asyncio
async def test_fake_upserts_by_external_id():
    fake = FakeProductReviews()
    first = await fake.upsert(_REVIEW)
    again = await fake.upsert(_REVIEW)
    assert first == again
    assert fake.rows[first]["status"] == "approved"
    assert await fake.upload_photo("a.jpg", b"x", "image/jpeg") == "https://fake.cdn/a.jpg"


@pytest.mark.asyncio
async def test_without_medusa_the_port_says_so_instead_of_pretending(monkeypatch):
    from src.platform.reviews import composition
    from src.platform.reviews.port import ProductReviewsUnavailableError

    def _no_settings():
        raise RuntimeError("MEDUSA_BASE_URL missing")

    monkeypatch.setattr(composition, "get_medusa_settings", _no_settings)
    port = composition.get_product_reviews_port()
    with pytest.raises(ProductReviewsUnavailableError):
        await port.upsert(_REVIEW)


@pytest.mark.asyncio
async def test_a_medusa_rejection_becomes_unavailable_for_the_operator():
    from src.platform.reviews.port import ProductReviewsUnavailableError

    client = HttpMedusaClient(base_url="https://m.test", admin_token="sk_x")
    with respx.mock(base_url="https://m.test") as r:
        r.post("/admin/product-reviews").mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
        with pytest.raises(ProductReviewsUnavailableError, match="404"):
            await MedusaProductReviews(client).upsert(_REVIEW)
    await client.aclose()
