"""La API del panel «Reseñas» (`/api/reviews`): moderar y medir.

Contrato que consume el dashboard (entity `review` del plugin): la lista por
estado con conteos, el resumen (invitaciones, reseñas, cupones, modo), publicar
/ ocultar / responder, y las fotos del cliente para moderarlas.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.promotions.grants import CouponGrant, FakeCouponGrantsStore
from src.platform.reviews.port import FakeProductReviews, ProductReviewsUnavailableError
from src.plugins.reviews import api as reviews_api
from src.plugins.reviews.agent.settings import ReviewSettings
from src.plugins.reviews.agent.store import ReviewRecord, ReviewsStore

NOW = 1_791_000_000_000
S = "wa_573001234567"


def _record(order_id: str, rating: int, **overrides) -> ReviewRecord:
    base = dict(
        order_id=order_id, display_id="#31", session_id=S, rating=rating, comment="Hermosa",
        publish_consent=True, author_name="Ana G.", author_city="Bogotá",
        products=({"product_id": "prod_1", "title": "Velón", "handle": "velon"},),
        photo_files=(), submitted_at_ms=NOW, wa_message_id=f"wamid.{order_id}",
    )
    base.update(overrides)
    return ReviewRecord(**base)


class _FakeRebuild:
    def __init__(self) -> None:
        self.calls: list[int] = []
        self.flushed: list[int] = []
        self.due: int | None = None

    async def __call__(self, now_ms: int, *, urgent: bool = False) -> bool:
        self.calls.append(now_ms)
        return True

    async def flush(self, now_ms: int) -> bool:
        if self.due is None or now_ms < self.due:
            return False
        self.flushed.append(now_ms)
        self.due = None
        return True

    def due_at_ms(self) -> int | None:
        return self.due

    def status(self) -> dict:
        return {"configured": True, "last_ok_at_ms": self.calls[-1] if self.calls else None,
                "last_error": None, "due_at_ms": self.due}


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = ReviewsStore(tmp_path)
    port = FakeProductReviews()
    grants = FakeCouponGrantsStore()
    rebuild = _FakeRebuild()
    rebuilds = rebuild.calls

    async def ready() -> bool:
        return True

    monkeypatch.setattr(reviews_api, "store", lambda: store)
    monkeypatch.setattr(reviews_api, "product_reviews", lambda: port)
    monkeypatch.setattr(reviews_api, "grants", lambda: grants)
    monkeypatch.setattr(reviews_api, "rebuild", lambda: rebuild)
    monkeypatch.setattr(reviews_api, "coupons_ready", ready)
    monkeypatch.setattr(reviews_api, "settings", lambda: ReviewSettings.from_env({"REVIEW_REQUESTS_MODE": "test"}))
    monkeypatch.setattr(reviews_api, "now_ms", lambda: NOW + 10)
    app = FastAPI()
    app.include_router(reviews_api.router, prefix="/api/reviews")
    return TestClient(app), store, port, grants, rebuilds


def test_list_reviews_by_status_with_counts(env):
    client, store, *_ = env
    store.save_review(_record("order_A", 5))
    store.save_review(_record("order_B", 2, submitted_at_ms=NOW + 5))
    store.update_review("rev_order_A", status="approved")

    data = client.get("/api/reviews/reviews", params={"status": "pending"}).json()

    assert [r["order_id"] for r in data["reviews"]] == ["order_B"]
    assert data["counts"] == {"pending": 1, "approved": 1, "hidden": 0}
    review = data["reviews"][0]
    assert review["review_id"] == "rev_order_B"
    assert review["photo_urls"] == []
    assert review["coupon"] is None and review["published"] is None


def test_photo_urls_point_to_the_plugin_photo_route(env):
    client, store, *_ = env
    store.save_review(_record("order_A", 5, photo_files=("photos/order_A_0.jpg",)))
    (store.root / "photos").mkdir(parents=True, exist_ok=True)
    (store.root / "photos" / "order_A_0.jpg").write_bytes(b"\xff\xd8img")

    [review] = client.get("/api/reviews/reviews").json()["reviews"]
    assert review["photo_urls"] == ["/api/reviews/photos/order_A_0.jpg"]
    resp = client.get("/api/reviews/photos/order_A_0.jpg")
    assert resp.status_code == 200 and resp.content == b"\xff\xd8img"
    assert client.get("/api/reviews/photos/..%2Fmetadata.json").status_code in (400, 404)


def test_approve_publishes_and_rebuilds(env):
    client, store, port, _grants, rebuilds = env
    store.save_review(_record("order_A", 5))

    resp = client.post("/api/reviews/reviews/rev_order_A/approve")

    assert resp.status_code == 200
    assert resp.json()["review"]["status"] == "approved"
    assert len(port.rows) == 1 and rebuilds == [NOW + 10]


def test_approve_failure_is_a_502_with_the_reason(env, monkeypatch):
    client, store, *_ = env
    store.save_review(_record("order_A", 5))

    class _Down(FakeProductReviews):
        async def upsert(self, review):
            raise ProductReviewsUnavailableError("Medusa respondió 404 — ¿está desplegado el módulo?")

    monkeypatch.setattr(reviews_api, "product_reviews", lambda: _Down())
    resp = client.post("/api/reviews/reviews/rev_order_A/approve")

    assert resp.status_code == 502
    assert "404" in resp.json()["detail"]
    assert store.get_review("rev_order_A").status == "pending"


def test_hide_and_reply(env):
    client, store, *_ = env
    store.save_review(_record("order_A", 4))

    assert client.post("/api/reviews/reviews/rev_order_A/reply", json={"reply": "¡Gracias!"}).json()["review"]["reply"] == "¡Gracias!"
    assert client.post("/api/reviews/reviews/rev_order_A/hide").json()["review"]["status"] == "hidden"
    assert client.post("/api/reviews/reviews/rev_nope/hide").status_code == 404


def test_summary_counts_invitations_reviews_and_coupons(env):
    client, store, _port, grants, _ = env
    store.claim_request("order_A", session_id=S, now_ms=NOW)
    store.update_request("order_A", status="sent", channel="flow")
    store.claim_request("order_B", session_id=S, now_ms=NOW)
    store.update_request("order_B", status="sent", channel="template")
    store.claim_request("order_C", session_id=S, now_ms=NOW)
    store.update_request("order_C", status="failed", channel="template")
    store.save_review(_record("order_A", 5, photo_files=("photos/x.jpg",), coupon={"code": "GRACIAS10", "percent": 10, "expires_at_ms": NOW}))
    store.save_review(_record("order_B", 2, coupon={"code": "GRACIAS5", "percent": 5, "expires_at_ms": NOW}))
    grants.grant(CouponGrant("GRACIAS10", S, 10, NOW, NOW + 1, "review", "order_A"))
    grants.mark_used(S, "GRACIAS10", "order_Z", NOW)

    data = client.get("/api/reviews/summary").json()

    assert data["requests"] == {"sent_flow": 1, "sent_template": 1, "failed": 1, "skipped": 0, "unconfirmed": 0, "total": 3}
    assert data["reviews"]["total"] == 2
    assert data["reviews"]["average_rating"] == 3.5
    assert data["reviews"]["distribution"] == {"1": 0, "2": 1, "3": 0, "4": 0, "5": 1}
    assert (data["reviews"]["with_photo"], data["reviews"]["low_rating"]) == (1, 1)
    assert data["coupons"] == {"granted": 2, "used": 1}
    assert data["settings"]["mode"] == "test"


def test_summary_says_why_invitations_did_not_go_out(env):
    """Premortem P4/I5: el panel dice por qué no salió una invitación (antes
    solo contaba las que salieron) y qué falta configurar."""
    client, store, *_ = env
    store.record_plan("order_A", session_id=S, action="skip", reason="disabled", at_ms=NOW)
    store.record_plan("order_B", session_id=S, action="wait", reason="coupon_not_configured", at_ms=NOW)
    store.record_plan("order_C", session_id=S, action="send_flow", reason="window_open", at_ms=NOW)
    store.claim_request("order_C", session_id=S, now_ms=NOW)  # se cayó a mitad del envío
    store.record_plan("order_D", session_id=S, action="skip", reason="already_requested", at_ms=NOW)

    data = client.get("/api/reviews/summary").json()

    assert data["not_invited"] == {"disabled": 1}
    assert data["waiting"] == {"coupon_not_configured": 1}
    assert data["requests"]["unconfirmed"] == 1
    assert data["settings"]["flow_configured"] is False
    assert data["settings"]["coupons_ready"] is True
    assert data["storefront"]["configured"] is True


def test_opening_the_panel_sends_a_due_storefront_rebuild(env):
    """Premortem M3: una reconstrucción que quedó pendiente sale al vencer."""
    client, _store, _port, _grants, _ = env
    rebuild = reviews_api.rebuild()
    rebuild.due = NOW
    client.get("/api/reviews/summary")
    assert rebuild.flushed == [NOW + 10]


@pytest.mark.asyncio
async def test_a_pending_rebuild_is_scheduled_once(monkeypatch):
    """Revisión de gates: aprobar en tanda programa UNA reconstrucción."""
    rebuild = _FakeRebuild()
    rebuild.due = NOW + 60_000
    monkeypatch.setattr(reviews_api, "now_ms", lambda: NOW)
    reviews_api._schedule_pending_rebuild(rebuild)
    reviews_api._schedule_pending_rebuild(rebuild)
    try:
        assert len(reviews_api._PENDING_FLUSHES) == 1
    finally:
        for task in list(reviews_api._PENDING_FLUSHES):
            task.cancel()
