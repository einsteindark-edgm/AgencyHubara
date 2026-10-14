"""Moderar y publicar reseñas (lo que hace el equipo desde el dashboard).

D6 del operador: se publican todas las que tengan permiso y no sean
ofensivas, también las de pocas estrellas, siempre con aprobación manual. Sin
la casilla de permiso, la reseña cuenta para el promedio pero no se muestra
(solo estrellas, anónima, sin foto). Al publicar se pide reconstruir la tienda
web (las fichas son estáticas), como mucho una vez cada 10 minutos.
"""
from __future__ import annotations

import pytest

from src.platform.reviews.port import FakeProductReviews, ProductReviewsUnavailableError
from src.plugins.reviews.agent.publishing import (
    StorefrontRebuild,
    hide_review,
    publish_review,
    reply_review,
)
from src.plugins.reviews.agent.store import ReviewRecord, ReviewsStore

NOW = 1_791_000_000_000


def _seed(store: ReviewsStore, **overrides) -> str:
    base = dict(
        order_id="order_01J", display_id="#31", session_id="wa_573001234567", rating=5,
        comment="Hermosa", publish_consent=True, author_name="Ana G.", author_city="Bogotá",
        products=({"product_id": "prod_1", "title": "Velón", "handle": "velon"},
                  {"product_id": "prod_2", "title": "Vela", "handle": "vela"}),
        photo_files=("photos/order_01J_0.jpg",), submitted_at_ms=NOW - 1000, wa_message_id="wamid.1",
    )
    base.update(overrides)
    record, _ = store.save_review(ReviewRecord(**base))
    (store.root / "photos").mkdir(parents=True, exist_ok=True)
    (store.root / "photos" / "order_01J_0.jpg").write_bytes(b"\xff\xd8photo")
    return record.review_id


class _Rebuild:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, now_ms: int) -> None:
        self.calls += 1


@pytest.mark.asyncio
async def test_publishing_writes_one_row_per_product_with_photo_and_rebuilds(tmp_path):
    store, port, rebuild = ReviewsStore(tmp_path), FakeProductReviews(), _Rebuild()
    review_id = _seed(store)

    record = await publish_review(store, port, review_id, now_ms=NOW, rebuild=rebuild)

    assert record.status == "approved"
    assert len(port.rows) == 2
    row = next(iter(port.rows.values()))
    assert row["external_id"].endswith(":prod_1")
    # Premortem M1: nada público lleva el id del pedido de Medusa.
    assert "order_01J" not in row["external_id"]
    assert (row["status"], row["rating"], row["body"], row["author_name"]) == ("approved", 5, "Hermosa", "Ana G.")
    assert row["order_display_id"] == 31
    assert row["photo_urls"] == ("https://fake.cdn/order_01J_0.jpg",)
    assert record.published["ids"] and record.published["error"] is None
    assert rebuild.calls == 1


@pytest.mark.asyncio
async def test_without_consent_only_the_stars_count(tmp_path):
    store, port = ReviewsStore(tmp_path), FakeProductReviews()
    review_id = _seed(store, publish_consent=False)

    await publish_review(store, port, review_id, now_ms=NOW, rebuild=_Rebuild())

    row = next(iter(port.rows.values()))
    assert (row["body"], row["author_name"], row["author_city"], row["photo_urls"]) == (
        None, "Cliente de Hubara", None, ())
    assert port.uploads == []


@pytest.mark.asyncio
async def test_medusa_failure_keeps_it_pending_with_the_reason(tmp_path):
    class _Down(FakeProductReviews):
        async def upsert(self, review):
            raise ProductReviewsUnavailableError("Medusa respondió 404")

    store = ReviewsStore(tmp_path)
    review_id = _seed(store)
    with pytest.raises(ProductReviewsUnavailableError):
        await publish_review(store, _Down(), review_id, now_ms=NOW, rebuild=_Rebuild())
    record = store.get_review(review_id)
    assert record.status == "pending"
    assert "404" in record.published["error"]


@pytest.mark.asyncio
async def test_hiding_a_published_review_hides_it_in_medusa(tmp_path):
    store, port = ReviewsStore(tmp_path), FakeProductReviews()
    review_id = _seed(store)
    await publish_review(store, port, review_id, now_ms=NOW, rebuild=_Rebuild())

    record = await hide_review(store, port, review_id, now_ms=NOW + 5, rebuild=_Rebuild())

    assert record.status == "hidden"
    assert {r["status"] for r in port.rows.values()} == {"hidden"}


@pytest.mark.asyncio
async def test_a_reply_on_a_published_review_reaches_the_store(tmp_path):
    store, port = ReviewsStore(tmp_path), FakeProductReviews()
    review_id = _seed(store)
    await publish_review(store, port, review_id, now_ms=NOW, rebuild=_Rebuild())

    record = await reply_review(store, port, review_id, "¡Gracias, Ana!", now_ms=NOW + 5, rebuild=_Rebuild())

    assert record.reply == "¡Gracias, Ana!"
    assert {r["reply"] for r in port.rows.values()} == {"¡Gracias, Ana!"}


@pytest.mark.asyncio
async def test_storefront_rebuild_is_debounced_and_skipped_without_a_hook(tmp_path):
    posts = []

    async def post(url):
        posts.append(url)

    rebuild = StorefrontRebuild(tmp_path, url="https://hooks.test/deploy", post=post, every_ms=600_000)
    await rebuild(NOW)
    await rebuild(NOW + 60_000)
    await rebuild(NOW + 601_000)
    assert posts == ["https://hooks.test/deploy", "https://hooks.test/deploy"]

    silent = StorefrontRebuild(tmp_path, url="PLACEHOLDER_set_out_of_band", post=post)
    await silent(NOW + 10_000_000)
    assert len(posts) == 2


@pytest.mark.asyncio
async def test_a_failing_deploy_hook_does_not_fail_the_publication(tmp_path):
    """Revisión L5: la reseña ya está en Medusa; el hook se reintenta después."""
    store, port = ReviewsStore(tmp_path), FakeProductReviews()
    review_id = _seed(store)

    async def broken(now_ms):
        raise RuntimeError("cloudflare 500")

    record = await publish_review(store, port, review_id, now_ms=NOW, rebuild=broken)
    assert record.status == "approved"


@pytest.mark.asyncio
async def test_a_failed_hook_call_is_not_counted_as_done(tmp_path):
    calls = []

    async def post(url):
        calls.append(url)
        if len(calls) == 1:
            raise RuntimeError("cloudflare 500")

    rebuild = StorefrontRebuild(tmp_path, url="https://hooks.test/deploy", post=post, every_ms=600_000)
    with pytest.raises(RuntimeError):
        await rebuild(NOW)
    await rebuild(NOW + 1000)
    assert len(calls) == 2


# ---------- premortem 2026-10-14 ----------


@pytest.mark.asyncio
async def test_an_upload_is_not_repeated_when_the_publication_is_retried(tmp_path):
    """M9: si Medusa falla después de subir la foto, el reintento no la sube
    otra vez (cada subida es un objeto público huérfano)."""
    store = ReviewsStore(tmp_path)
    review_id = _seed(store)

    class _FlakyUpsert(FakeProductReviews):
        def __init__(self):
            super().__init__()
            self.fail = True

        async def upsert(self, review):
            if self.fail:
                self.fail = False
                raise ProductReviewsUnavailableError("Medusa respondió 503")
            return await super().upsert(review)

    port = _FlakyUpsert()
    with pytest.raises(ProductReviewsUnavailableError):
        await publish_review(store, port, review_id, now_ms=NOW, rebuild=_Rebuild())
    await publish_review(store, port, review_id, now_ms=NOW + 5, rebuild=_Rebuild())
    assert len(port.uploads) == 1


@pytest.mark.asyncio
async def test_hiding_after_a_half_done_publication_hides_every_row(tmp_path):
    """M5: la primera fila quedó publicada y la segunda falló; «Ocultar» tiene
    que bajar las dos (antes no tocaba Medusa porque no había ids)."""
    store = ReviewsStore(tmp_path)
    review_id = _seed(store)

    class _SecondFails(FakeProductReviews):
        async def upsert(self, review):
            if review.product_id == "prod_2" and review.status == "approved":
                raise ProductReviewsUnavailableError("Medusa no respondió")
            return await super().upsert(review)

    port = _SecondFails()
    with pytest.raises(ProductReviewsUnavailableError):
        await publish_review(store, port, review_id, now_ms=NOW, rebuild=_Rebuild())
    await hide_review(store, port, review_id, now_ms=NOW + 5, rebuild=_Rebuild())
    assert {r["status"] for r in port.rows.values()} == {"hidden"}


@pytest.mark.asyncio
async def test_a_review_without_publishable_products_is_not_marked_published(tmp_path):
    """M11: sin productos con id (borrados) no se dice «publicada»."""
    store, port = ReviewsStore(tmp_path), FakeProductReviews()
    review_id = _seed(store, products=({"product_id": None, "title": "Velón borrado", "handle": None},))
    with pytest.raises(ProductReviewsUnavailableError, match="productos"):
        await publish_review(store, port, review_id, now_ms=NOW, rebuild=_Rebuild())
    assert store.get_review(review_id).status == "pending"


@pytest.mark.asyncio
async def test_a_moderation_inside_the_window_leaves_the_rebuild_scheduled(tmp_path):
    """M3/W4: aprobar en tanda no deja la tienda sin reconstruir: lo que cae
    dentro de los 10 minutos queda pendiente y sale al vencer."""
    posts = []

    async def post(url):
        posts.append(url)

    rebuild = StorefrontRebuild(tmp_path, url="https://hooks.test/deploy", post=post, every_ms=600_000)
    await rebuild(NOW)
    await rebuild(NOW + 60_000)
    assert len(posts) == 1
    assert rebuild.due_at_ms() == NOW + 600_000
    assert await rebuild.flush(NOW + 300_000) is False
    assert await rebuild.flush(NOW + 600_000) is True
    assert len(posts) == 2 and rebuild.due_at_ms() is None


@pytest.mark.asyncio
async def test_hiding_rebuilds_at_once(tmp_path):
    """W4: una reseña ocultada (datos personales, foto equivocada) no espera."""
    posts = []

    async def post(url):
        posts.append(url)

    rebuild = StorefrontRebuild(tmp_path, url="https://hooks.test/deploy", post=post, every_ms=600_000)
    await rebuild(NOW)
    await rebuild(NOW + 60_000, urgent=True)
    assert len(posts) == 2


@pytest.mark.asyncio
async def test_the_deploy_hook_status_is_checked_and_remembered(tmp_path):
    """M8: un hook revocado (4xx/5xx) no cuenta como reconstruida y queda el
    error a la vista."""
    import httpx

    from src.plugins.reviews.agent.publishing import post_hook

    transport = httpx.MockTransport(lambda request: httpx.Response(404, text="hook not found"))
    rebuild = StorefrontRebuild(
        tmp_path, url="https://hooks.test/deploy", post=lambda url: post_hook(url, transport=transport)
    )
    with pytest.raises(httpx.HTTPStatusError):
        await rebuild(NOW)
    status = rebuild.status()
    assert status["last_ok_at_ms"] is None and "404" in status["last_error"]
