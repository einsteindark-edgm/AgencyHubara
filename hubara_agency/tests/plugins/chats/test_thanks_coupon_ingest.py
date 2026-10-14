"""El cupón de agradecimiento por calificar se aplica solo al escribirnos.

Reseñas tras la entrega (2026-10-13): el mensaje de gracias promete «cuando nos
escribas, lo aplicamos solo». Igual que el cupón de una campaña (2g): al llegar
un mensaje de quien tiene un cupón personal vigente, queda en el episodio
(RMW atómico) y la nota de cada turno se lo dice al bot. Nunca pisa un cupón
ya aplicado y nunca tumba el ingest.
"""
from __future__ import annotations

import time

import pytest

from src.plugins.chats.agent.sales.use_cases.coupon_application import CouponApplication
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage
from tests.plugins.chats.test_campaign_reply import (
    _SESSION,
    _History,
    _Loader,
    _message,
    _Store,
)


def _thanks_promo():
    from src.platform.promotions.port import PromotionDTO

    return PromotionDTO(
        id="promo_gracias5", code="GRACIAS5", discount_type="percentage", value=5,
        currency_code=None, target_type="items", allocation="across", max_quantity=None,
        product_ids=(), variant_ids=(), collection_ids=(), min_subtotal_cop=None,
        is_automatic=False, status="active", starts_at_ms=None, ends_at_ms=None,
        budget_type=None, budget_limit=None, budget_used=None,
        description="Gracias por calificar", granted_only=True,
    )


class _ThanksApplier:
    def __init__(self, result=None, *, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[str] = []

    async def __call__(self, session_id: str, now_ms: int):
        self.calls.append(session_id)
        if self.error is not None:
            raise self.error
        return self.result


def _metadata(now_ms: int, *, coupon: dict | None = None) -> dict:
    episode = {"episode_id": "ep_007", "started_at_ms": now_ms - 60_000, "closed_at_ms": None}
    if coupon is not None:
        episode["applied_coupon"] = coupon
    return {"active_route": "ventas", "last_inbound_at_ms": now_ms - 60_000, "episodes": [episode]}


def _use_case(store, loader, applier) -> IngestInboundMessage:
    return IngestInboundMessage(
        history_store=_History(),  # type: ignore[arg-type]
        load_session=loader,  # type: ignore[arg-type]
        metadata_store=store,  # type: ignore[arg-type]
        thanks_coupon=applier,
    )


@pytest.mark.asyncio
async def test_the_thanks_coupon_is_applied_without_asking_for_it():
    now = int(time.time() * 1000)
    store = _Store(_metadata(now))
    loader = _Loader()
    applier = _ThanksApplier(CouponApplication("GRACIAS5", None, _thanks_promo()))

    await _use_case(store, loader, applier).execute(_message("Hola, quiero otra vela"))

    assert applier.calls == [_SESSION]
    assert store.data[_SESSION]["episodes"][-1]["applied_coupon"]["code"] == "GRACIAS5"
    context = "\n".join(loader.calls[0]["extra_context"])
    assert "GRACIAS5" in context


@pytest.mark.asyncio
async def test_an_already_applied_coupon_is_not_replaced():
    now = int(time.time() * 1000)
    store = _Store(_metadata(now, coupon={"code": "BIENVENIDA"}))
    applier = _ThanksApplier(CouponApplication("GRACIAS5", None, _thanks_promo()))

    await _use_case(store, _Loader(), applier).execute(_message("Hola"))

    assert applier.calls == []
    assert store.data[_SESSION]["episodes"][-1]["applied_coupon"]["code"] == "BIENVENIDA"


@pytest.mark.asyncio
async def test_no_grant_or_a_failure_applies_nothing_and_the_message_still_flows():
    now = int(time.time() * 1000)
    for applier in (_ThanksApplier(None), _ThanksApplier(error=RuntimeError("medusa caída"))):
        store = _Store(_metadata(now))
        loader = _Loader()

        await _use_case(store, loader, applier).execute(_message("Hola"))

        assert "applied_coupon" not in store.data[_SESSION]["episodes"][-1]
        assert len(loader.calls) == 1


@pytest.mark.asyncio
async def test_webhook_ingest_applies_thanks_coupons_with_the_sdk_ports(monkeypatch):
    """Gotcha #1 (tests verdes, feature muerta): el webhook real lee la
    concesión y valida el cupón por los puertos del SDK."""
    import src.plugins.chats.agent.sales.composition as comp
    import src.sdk.connectorkit as ck
    from src.platform.promotions.grants import CouponGrant, FakeCouponGrantsStore
    from src.platform.promotions.port import FakePromotionsPort
    from src.platform.promotions.quota_store import FakePromoQuotaStore

    now = int(time.time() * 1000)
    grants = FakeCouponGrantsStore()
    grants.grant(CouponGrant(code="GRACIAS5", session_id=_SESSION, percent=5, granted_at_ms=now - 1,
                             expires_at_ms=now + 86_400_000, source="review", reference="order_01A"))
    monkeypatch.setattr(ck, "get_promotions_port", lambda: FakePromotionsPort([_thanks_promo()]))
    monkeypatch.setattr(ck, "get_promo_quota_store", lambda: FakePromoQuotaStore())
    monkeypatch.setattr(ck, "get_coupon_sales_reader", lambda: None)
    monkeypatch.setattr(ck, "get_catalog_client", lambda: None)
    monkeypatch.setattr(ck, "get_web_cart_reader", lambda: None)
    monkeypatch.setattr(ck, "get_coupon_grants_store", lambda: grants)
    monkeypatch.setattr(comp, "_INGEST_USE_CASE", None)

    use_case = comp.build_ingest_use_case()
    mine = await use_case._check_thanks_coupon(_SESSION, now)
    nobody = await use_case._check_thanks_coupon("wa_573009999999", now)

    assert mine is not None and (mine.applied, mine.code) == (True, "GRACIAS5")
    assert nobody is None
