"""El registro de invitaciones y reseñas (`_reviews/` en el vault).

Una invitación por pedido (reclamarla es atómico: dos despertares del flujo o
un reintento no mandan dos), una reseña por pedido (la respuesta repetida de
Meta no la duplica) y la lista que modera el equipo en el dashboard.
"""
from __future__ import annotations

import pytest

from src.plugins.reviews.agent.store import ReviewRecord, ReviewsStore

NOW = 1_760_000_000_000


@pytest.fixture
def store(tmp_path):
    return ReviewsStore(tmp_path)


def test_a_request_is_claimed_once(store):
    assert store.claim_request("order_01A", session_id="wa_573001234567", now_ms=NOW) is True
    assert store.claim_request("order_01A", session_id="wa_573001234567", now_ms=NOW + 1) is False
    assert store.get_request("order_01A")["status"] == "claimed"


def test_request_outcome_is_recorded(store):
    store.claim_request("order_01A", session_id="wa_573001234567", now_ms=NOW)
    store.update_request("order_01A", status="sent", channel="template", sent_at_ms=NOW + 5)
    req = store.get_request("order_01A")
    assert (req["status"], req["channel"], req["sent_at_ms"]) == ("sent", "template", NOW + 5)
    assert req["session_id"] == "wa_573001234567"


def _review(order_id: str = "order_01A", **overrides) -> ReviewRecord:
    base = dict(
        order_id=order_id,
        display_id="#31",
        session_id="wa_573001234567",
        rating=5,
        comment="Hermosa",
        publish_consent=True,
        author_name="Ana G.",
        author_city="Bogotá",
        products=({"product_id": "prod_1", "title": "Velón", "handle": "velon"},),
        photo_files=(),
        submitted_at_ms=NOW,
        wa_message_id="wamid.1",
    )
    base.update(overrides)
    return ReviewRecord(**base)


def test_a_review_is_saved_once_per_order(store):
    first, created = store.save_review(_review())
    again, created_again = store.save_review(_review(rating=1, wa_message_id="wamid.2"))
    assert created is True and created_again is False
    assert again.rating == 5  # la primera respuesta manda
    assert first.review_id == "rev_order_01A"
    assert first.status == "pending"


def test_reviews_are_listed_newest_first_and_filtered_by_status(store):
    store.save_review(_review("order_01A", submitted_at_ms=NOW))
    store.save_review(_review("order_01B", submitted_at_ms=NOW + 10))
    store.update_review("rev_order_01A", status="approved")
    assert [r.order_id for r in store.list_reviews()] == ["order_01B", "order_01A"]
    assert [r.order_id for r in store.list_reviews(status="approved")] == ["order_01A"]


def test_unknown_review_update_returns_none(store):
    assert store.update_review("rev_nope", status="approved") is None


def test_an_order_id_cannot_build_a_path(store):
    with pytest.raises(ValueError):
        store.claim_request("../../metadata", session_id="wa_1", now_ms=NOW)
