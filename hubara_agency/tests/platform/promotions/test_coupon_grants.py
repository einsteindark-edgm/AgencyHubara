"""Cupones personales: una concesión por persona, de un uso y con vencimiento.

Reseñas tras la entrega (2026-10-13): quien califica su pedido recibe 5 %
(10 % con foto) para la próxima compra. El código es uno solo (GRACIAS5) pero
solo lo puede usar quien lo recibió, una vez y antes de que venza. Medusa no
cuenta usos de pedidos borrador (los del bot) ni limita por persona, así que la
concesión vive en el vault: `_promotions/grants/<sesión>.json`.

Contrato común al doble (Fake) y al de disco (Vault).
"""
from __future__ import annotations

import pytest

from src.platform.promotions.grants import (
    CouponGrant,
    FakeCouponGrantsStore,
    VaultCouponGrantsStore,
)

S = "wa_573001234567"
NOW = 1_760_000_000_000
DAY = 86_400_000


@pytest.fixture(params=["fake", "vault"])
def store(request, tmp_path):
    if request.param == "fake":
        return FakeCouponGrantsStore()
    return VaultCouponGrantsStore(tmp_path)


def _grant(**overrides) -> CouponGrant:
    base = dict(
        code="gracias5",
        session_id=S,
        percent=5,
        granted_at_ms=NOW,
        expires_at_ms=NOW + 60 * DAY,
        source="review",
        reference="order_01A",
    )
    base.update(overrides)
    return CouponGrant(**base)


def test_granted_coupon_is_active_for_that_person_only(store):
    store.grant(_grant())
    assert store.active(S, "GRACIAS5", NOW + DAY).code == "GRACIAS5"
    assert store.active("wa_573009876543", "GRACIAS5", NOW + DAY) is None


def test_grant_is_idempotent_per_reference(store):
    store.grant(_grant())
    store.grant(_grant(granted_at_ms=NOW + 5))
    assert len(store.list_for(S)) == 1


def test_expired_grant_is_not_active(store):
    store.grant(_grant())
    assert store.active(S, "GRACIAS5", NOW + 60 * DAY) is None


def test_used_grant_is_not_active_and_use_is_idempotent(store):
    store.grant(_grant())
    used = store.mark_used(S, "GRACIAS5", "order_02B", NOW + DAY)
    assert used is not None and used.used_order_id == "order_02B"
    assert store.active(S, "GRACIAS5", NOW + 2 * DAY) is None
    again = store.mark_used(S, "GRACIAS5", "order_02B", NOW + 3 * DAY)
    assert again is not None and again.used_at_ms == NOW + DAY


def test_two_grants_are_used_one_at_a_time(store):
    store.grant(_grant(reference="order_01A"))
    store.grant(_grant(reference="order_01C", expires_at_ms=NOW + 90 * DAY))
    store.mark_used(S, "GRACIAS5", "order_02B", NOW + DAY)
    remaining = store.active(S, "GRACIAS5", NOW + DAY)
    assert remaining is not None and remaining.reference == "order_01C"


def test_first_active_with_the_same_discount_returns_the_one_that_expires_first(store):
    store.grant(_grant(code="GRACIAS5", reference="r1", expires_at_ms=NOW + 30 * DAY))
    store.grant(_grant(code="GRACIAS5", reference="r2", expires_at_ms=NOW + 10 * DAY))
    assert store.first_active(S, NOW).reference == "r2"


def test_mark_used_without_an_active_grant_returns_none(store):
    assert store.mark_used(S, "GRACIAS5", "order_02B", NOW) is None


def test_session_id_cannot_build_a_path(tmp_path):
    with pytest.raises(ValueError):
        VaultCouponGrantsStore(tmp_path).list_for("../../etc")


def test_first_active_prefers_the_biggest_discount_then_the_earliest_expiry(store):
    """Premortem C8: la de 10 % (con foto) gana a la de 5 % que vence antes."""
    store.grant(_grant(code="gracias5", reference="order_01A", expires_at_ms=NOW + 2 * DAY))
    store.grant(_grant(code="gracias10", percent=10, reference="order_01B", expires_at_ms=NOW + 30 * DAY))
    store.grant(_grant(code="gracias10", percent=10, reference="order_01C", expires_at_ms=NOW + 9 * DAY))

    first = store.first_active(S, NOW + DAY)

    assert (first.code, first.reference) == ("GRACIAS10", "order_01C")
