"""Composición de la central de cupones: el deployment decide el adapter."""
from __future__ import annotations

from datetime import date, datetime

import pytest

from src.platform.promotions import composition
from src.platform.promotions.admin import MedusaPromotionsAdmin
from src.platform.promotions.coupon import CouponSpec
from src.platform.promotions.port import PromotionsUnavailableError


@pytest.fixture(autouse=True)
def _fresh_factories():
    factories = (composition.get_promotions_port, composition.get_promotions_admin_port)
    for factory in factories:
        factory.cache_clear()
    yield
    for factory in factories:
        factory.cache_clear()


@pytest.mark.asyncio
async def test_admin_port_without_medusa_fails_loud_instead_of_pretending(monkeypatch) -> None:
    def no_settings():
        raise RuntimeError("MEDUSA_BASE_URL missing")

    monkeypatch.setattr(composition, "get_medusa_settings", no_settings)
    admin = composition.get_promotions_admin_port()

    with pytest.raises(PromotionsUnavailableError):
        await admin.list_coupons()
    with pytest.raises(PromotionsUnavailableError):
        await admin.create_coupon(
            CouponSpec("AMOR27", "AMOR27", 10, None, date(2026, 9, 22), date(2026, 9, 27))
        )


def test_admin_port_with_medusa_invalidates_the_local_reader_on_write(monkeypatch) -> None:
    class Settings:
        base_url = "http://medusa.test"

    calls: list[str] = []

    class Reader:
        def invalidate(self) -> None:
            calls.append("invalidate")

    monkeypatch.setattr(composition, "get_medusa_settings", lambda: Settings())
    monkeypatch.setattr(composition, "get_medusa_client", lambda: object())
    monkeypatch.setattr(composition, "get_promotions_port", lambda: Reader())

    admin = composition.get_promotions_admin_port()
    assert isinstance(admin, MedusaPromotionsAdmin)
    admin._changed()

    assert calls == ["invalidate"]


def test_quota_store_audit_and_lock_live_in_the_vault(_isolate_vault_dir) -> None:
    store = composition.get_promo_quota_store()
    audit = composition.get_coupon_audit_log()

    store.replace("promo_1", "AMOR27", [], show_units_left=True, actor="ana", now_iso="t")
    audit.append(actor="ana", action="create", promotion_id="promo_1", code="AMOR27")

    assert (_isolate_vault_dir / "_promotions" / "quotas" / "promo_1.json").exists()
    assert (_isolate_vault_dir / "_promotions" / "audit.jsonl").exists()
    assert composition.get_quota_lock() is not None


@pytest.mark.asyncio
async def test_sales_reader_without_medusa_is_unavailable(monkeypatch) -> None:
    composition.get_coupon_sales_reader.cache_clear()

    def no_settings():
        raise RuntimeError("MEDUSA_BASE_URL missing")

    monkeypatch.setattr(composition, "get_medusa_settings", no_settings)
    reader = composition.get_coupon_sales_reader()
    composition.get_coupon_sales_reader.cache_clear()

    with pytest.raises(PromotionsUnavailableError):
        await reader.sold_units(since=datetime(2026, 9, 22))


def test_coupon_conditions_live_in_the_vault(_isolate_vault_dir) -> None:
    from src.platform.promotions.conditions import CouponConditions

    composition.get_coupon_conditions_store().put(CouponConditions("BIENVENIDA", first_purchase_only=True))

    assert (_isolate_vault_dir / "_promotions" / "conditions" / "BIENVENIDA.json").exists()


@pytest.mark.asyncio
async def test_the_bot_reader_carries_the_conditions_saved_in_the_vault(monkeypatch, _isolate_vault_dir) -> None:
    from src.platform.promotions.conditions import CouponConditions
    from src.platform.promotions.port import FakePromotionsPort, PromotionDTO

    class Settings:
        base_url = "http://medusa.test"

    welcome = PromotionDTO(
        id="promo_1", code="BIENVENIDA", discount_type="percentage", value=5, currency_code="cop",
        target_type="items", allocation="across", max_quantity=None, product_ids=(), variant_ids=(),
        collection_ids=(), min_subtotal_cop=None, is_automatic=False, status="active", starts_at_ms=None,
        ends_at_ms=None, budget_type=None, budget_limit=None, budget_used=None, description="Bienvenida",
    )
    monkeypatch.setattr(composition, "get_medusa_settings", lambda: Settings())
    monkeypatch.setattr(composition, "get_medusa_client", lambda: object())
    monkeypatch.setattr(composition, "MedusaPromotionsPort", lambda _client: FakePromotionsPort([welcome]))
    composition.get_coupon_conditions_store().put(CouponConditions("BIENVENIDA", first_purchase_only=True))

    [promo] = await composition.get_promotions_port().list_active()

    assert promo.first_purchase_only is True
