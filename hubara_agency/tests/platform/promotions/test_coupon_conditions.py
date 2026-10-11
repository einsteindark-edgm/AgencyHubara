"""Condiciones de un cupón que viven en Hubara: «solo primera compra».

Caso del 2026-10-09: la página ofrece un 5 % de bienvenida para la primera
compra y el cupón no existía; la clienta lo pidió por el chat, el bot dijo que
no había descuento y un colega tuvo que intervenir. Medusa no guarda metadata
en una promoción ni cuenta los pedidos borrador del bot, así que la condición
va al vault y el lector de promociones la pega en cada cupón: la ven el bot,
la campaña y la app del operador por el mismo camino.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.platform.promotions.conditions import (
    ConditionedPromotionsPort,
    CouponConditions,
    CouponConditionsError,
    CouponConditionsStore,
    FakeCouponConditionsStore,
    VaultCouponConditionsStore,
)
from src.platform.promotions.port import FakePromotionsPort, PromotionDTO


@pytest.fixture(params=["fake", "vault"])
def store(request, tmp_path: Path) -> CouponConditionsStore:
    return FakeCouponConditionsStore() if request.param == "fake" else VaultCouponConditionsStore(tmp_path)


def test_conditions_store_contract(store: CouponConditionsStore) -> None:
    assert store.get("BIENVENIDA") == CouponConditions("BIENVENIDA")

    saved = store.put(
        CouponConditions("bienvenida", first_purchase_only=True, updated_at="2026-10-10T12:00:00Z", updated_by="ana")
    )

    assert saved == CouponConditions("BIENVENIDA", True, "2026-10-10T12:00:00Z", "ana")
    # El código no distingue mayúsculas (el cliente y la central lo normalizan).
    assert store.get("Bienvenida") == saved
    assert store.get("AMOR2026") == CouponConditions("AMOR2026")

    store.delete("BIENVENIDA")
    assert store.get("BIENVENIDA") == CouponConditions("BIENVENIDA")


def test_a_broken_conditions_file_fails_closed(tmp_path: Path) -> None:
    store = VaultCouponConditionsStore(tmp_path)
    store.put(CouponConditions("BIENVENIDA", first_purchase_only=True))
    (tmp_path / "_promotions" / "conditions" / "BIENVENIDA.json").write_text("{roto", encoding="utf-8")

    with pytest.raises(CouponConditionsError):
        store.get("BIENVENIDA")


def test_a_code_that_could_build_a_path_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        VaultCouponConditionsStore(tmp_path).get("../metadata")


def _promo(code: str) -> PromotionDTO:
    return PromotionDTO(
        id=f"promo_{code.lower()}", code=code, discount_type="percentage", value=5, currency_code="cop",
        target_type="items", allocation="across", max_quantity=None, product_ids=(), variant_ids=(),
        collection_ids=(), min_subtotal_cop=None, is_automatic=False, status="active", starts_at_ms=None,
        ends_at_ms=None, budget_type=None, budget_limit=None, budget_used=None, description="Bienvenida",
    )


@pytest.mark.asyncio
async def test_the_promotions_reader_carries_the_condition_of_each_coupon() -> None:
    conditions = FakeCouponConditionsStore()
    conditions.put(CouponConditions("BIENVENIDA", first_purchase_only=True))
    port = ConditionedPromotionsPort(FakePromotionsPort([_promo("BIENVENIDA"), _promo("AMOR2026")]), lambda: conditions)

    by_code = {p.code: p for p in await port.list_active()}

    assert by_code["BIENVENIDA"].first_purchase_only is True
    assert by_code["AMOR2026"].first_purchase_only is False
    welcome = await port.get_by_code("bienvenida")
    assert welcome is not None and welcome.first_purchase_only is True
    assert await port.get_by_code("NOEXISTE") is None


class _BrokenConditions(FakeCouponConditionsStore):
    def get(self, code: str) -> CouponConditions:
        raise CouponConditionsError("ilegible")


@pytest.mark.asyncio
async def test_a_coupon_whose_conditions_cannot_be_read_is_not_offered() -> None:
    port = ConditionedPromotionsPort(FakePromotionsPort([_promo("BIENVENIDA")]), _BrokenConditions)

    [promo] = await port.list_active()

    # Falla cerrada, como las reglas de Medusa que no se pudieron leer.
    assert promo.scope_unresolved is True


@pytest.mark.asyncio
async def test_the_reader_keeps_the_cache_invalidation_of_the_inner_port() -> None:
    calls: list[str] = []

    class _Inner(FakePromotionsPort):
        def invalidate(self) -> None:
            calls.append("invalidate")

    ConditionedPromotionsPort(_Inner([]), FakeCouponConditionsStore).invalidate()

    assert calls == ["invalidate"]


@pytest.mark.asyncio
async def test_a_code_that_cannot_have_conditions_is_offered_as_always(tmp_path: Path) -> None:
    # Un cupón creado a mano en Medusa con un código que la central no usaría
    # («AMOR-26») no puede tener condiciones guardadas: se ofrece como siempre.
    port = ConditionedPromotionsPort(
        FakePromotionsPort([_promo("AMOR-26")]), lambda: VaultCouponConditionsStore(tmp_path)
    )

    [promo] = await port.list_active()

    assert promo.scope_unresolved is False
    assert promo.first_purchase_only is False
