"""Gotcha #1 (tests verdes, feature muerta): las dependencias reales del worker
de reseñas se arman con los puertos del SDK, y el paso al equipo llega al
contrato de chats con identidad de servicio."""
from __future__ import annotations

import pytest

import src.sdk.connectorkit as ck
from src.platform.promotions.grants import FakeCouponGrantsStore


def test_real_deps_are_built_from_the_sdk_ports(monkeypatch, tmp_path):
    from src.plugins.reviews.agent import composition

    monkeypatch.setattr(ck, "get_order_facts_port", lambda: "FACTS")
    monkeypatch.setattr(ck, "get_order_query_port", lambda: "ORDERS")
    monkeypatch.setattr(ck, "get_coupon_grants_store", lambda: FakeCouponGrantsStore())
    monkeypatch.setenv("REVIEW_REQUESTS_MODE", "test")

    deps = composition.get_reviews_deps()

    assert (deps.order_facts, deps.order_query) == ("FACTS", "ORDERS")
    assert deps.settings.mode == "test"
    assert callable(deps.send_flow) and callable(deps.send_template) and callable(deps.send_cta)


@pytest.mark.asyncio
async def test_low_rating_escalation_posts_to_the_chats_contract(monkeypatch):
    from src.plugins.reviews.api import chats_cast

    calls = []

    async def fake_forward(request, method, path, **kwargs):
        calls.append((request, method, path, kwargs))
        return {"escalated": True}

    monkeypatch.setattr(chats_cast.castkit, "forward", fake_forward)
    monkeypatch.setenv("HUBARA_API_BASE_URL", "http://api:8000")

    await chats_cast.escalate_low_rating("wa_573001234567", "Calificó 2★ el pedido #31")

    [(request, method, path, kwargs)] = calls
    assert (request, method) == (None, "POST")
    assert path == "/api/chats/session-actions/wa_573001234567/escalate"
    assert kwargs["auth"] == "service"
    assert kwargs["base_url"] == "http://api:8000"
    assert kwargs["body"] == {"reason_category": "LOW_RATING", "summary": "Calificó 2★ el pedido #31"}


def test_quiet_hours_end_is_none_by_day_and_the_morning_by_night():
    from src.plugins.reviews.agent.composition import _quiet_hours_end

    noon_bogota = 1_791_000_000_000 - (1_791_000_000_000 % 86_400_000) + 17 * 3_600_000  # 12:00 COT
    assert _quiet_hours_end("wa_573001234567", noon_bogota) is None
    eleven_pm = noon_bogota + 11 * 3_600_000  # 23:00 COT
    end = _quiet_hours_end("wa_573001234567", eleven_pm)
    assert end is not None and end - eleven_pm == 9 * 3_600_000  # 08:00
