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

    assert await chats_cast.escalate_low_rating("wa_573001234567", "Calificó 2★ el pedido #31") is True

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


@pytest.mark.asyncio
async def test_coupons_are_ready_only_when_both_are_active_and_personal(monkeypatch):
    from dataclasses import replace

    from src.platform.promotions.port import FakePromotionsPort
    from src.plugins.reviews.agent.composition import coupons_ready
    from tests.plugins.chats.sales.test_coupon_tools import _promo

    g5 = _promo(id="p5", code="GRACIAS5", value=5, granted_only=True)
    g10 = _promo(id="p10", code="GRACIAS10", value=10, granted_only=True)
    now = 1_791_000_000_000

    assert await coupons_ready(FakePromotionsPort([g5, g10]), ("GRACIAS5", "GRACIAS10"), now) is True
    assert await coupons_ready(FakePromotionsPort([g5]), ("GRACIAS5", "GRACIAS10"), now) is False
    public = replace(g10, granted_only=False)
    assert await coupons_ready(FakePromotionsPort([g5, public]), ("GRACIAS5", "GRACIAS10"), now) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "confirmed"),
    [({"escalated": True}, True), ({"escalated": False, "already_human": True}, True),
     ({"escalated": False, "already_human": False}, False), ({}, False)],
)
async def test_the_escalation_counts_only_when_chats_confirms_it(monkeypatch, answer, confirmed):
    """Premortem P2: el equipo solo «quedó avisado» si chats lo confirma."""
    from src.plugins.reviews.api import chats_cast

    async def fake_forward(request, method, path, **kwargs):
        return answer

    monkeypatch.setattr(chats_cast.castkit, "forward", fake_forward)
    assert await chats_cast.escalate_low_rating("wa_573001234567", "x") is confirmed


@pytest.mark.asyncio
async def test_coupons_must_last_as_long_as_what_is_promised(monkeypatch):
    """Premortem P9: el cupón que se promete vale 60 días; una promoción que
    termina antes (o se agota) no está lista para invitar."""
    from dataclasses import replace

    from src.platform.promotions.port import FakePromotionsPort
    from src.plugins.reviews.agent.composition import coupons_ready
    from tests.plugins.chats.sales.test_coupon_tools import _promo

    day = 86_400_000
    now = 1_791_000_000_000
    g5 = _promo(id="p5", code="GRACIAS5", value=5, granted_only=True)
    g10 = _promo(id="p10", code="GRACIAS10", value=10, granted_only=True)
    ends_soon = replace(g10, ends_at_ms=now + 30 * day)

    codes = ("GRACIAS5", "GRACIAS10")
    assert await coupons_ready(FakePromotionsPort([g5, ends_soon]), codes, now, valid_for_ms=62 * day) is False
    assert await coupons_ready(FakePromotionsPort([g5, replace(g10, ends_at_ms=now + 90 * day)]), codes, now,
                               valid_for_ms=62 * day) is True


@pytest.mark.asyncio
async def test_the_product_photo_fetch_is_capped_and_https_only():
    """Premortem P11: la foto del producto para la tarjeta se lee con tope (sin
    cargar un archivo enorme en memoria) y solo por https."""
    import httpx

    from src.plugins.reviews.agent.composition import _fetch_url

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/big.jpg":
            return httpx.Response(200, content=b"x" * (9 * 1024 * 1024))
        return httpx.Response(200, content=b"\xff\xd8ok")

    transport = httpx.MockTransport(handler)
    assert await _fetch_url("https://cdn.example/ok.jpg", transport=transport) == b"\xff\xd8ok"
    assert await _fetch_url("https://cdn.example/big.jpg", transport=transport) is None
    assert await _fetch_url("http://169.254.169.254/latest/meta-data", transport=transport) is None
    assert seen == ["https://cdn.example/ok.jpg", "https://cdn.example/big.jpg"]
