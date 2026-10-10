"""Central de cupones: «solo primera compra» (caso del 2026-10-09).

La página ofrece un 5 % de bienvenida en la primera compra y el cupón no
existía. La central lo marca al crear o editar el cupón; la condición vive en
Hubara (Medusa no guarda metadata en promociones ni cuenta pedidos borrador)
y se escribe ANTES de crear el cupón en Medusa: si el vault falla, no queda un
cupón sin su condición.
"""
from __future__ import annotations

from typing import Any

import src.plugins.marketing.api.coupons as coupons_api
from src.sdk.connectorkit import CouponConditions, CouponConditionsError, FakeCouponConditionsStore
from tests.plugins.marketing.test_coupons_api import _BODY, World, _create, _tagged_amor26, _world, await_

_WELCOME = {**_BODY, "code": "bienvenida", "campaign_name": "Descuento de bienvenida", "percentage": 5,
            "products": "all", "first_purchase_only": True}


def _with_conditions(monkeypatch, **kw: Any) -> tuple[World, FakeCouponConditionsStore]:
    w = _world(monkeypatch, **kw)
    conditions = FakeCouponConditionsStore()
    monkeypatch.setattr(coupons_api, "conditions_store", lambda: conditions)
    return w, conditions


def test_a_welcome_coupon_is_created_only_for_first_purchases(monkeypatch) -> None:
    w, conditions = _with_conditions(monkeypatch)

    res = w.client.post("/api/marketing/coupons", json=_WELCOME)

    assert res.status_code == 201, res.text
    assert res.json()["first_purchase_only"] is True
    assert conditions.get("BIENVENIDA").first_purchase_only is True
    [entry] = w.audit.entries()
    assert entry.action == "create" and entry.detail["first_purchase_only"] is True


def test_a_coupon_for_everybody_clears_an_old_condition_of_the_same_code(monkeypatch) -> None:
    w, conditions = _with_conditions(monkeypatch)
    conditions.put(CouponConditions("AMOR27", first_purchase_only=True))  # de un cupón borrado

    created = _create(w)

    assert created["first_purchase_only"] is False
    assert conditions.get("AMOR27").first_purchase_only is False


def test_a_taken_code_does_not_change_the_condition_of_the_existing_coupon(monkeypatch) -> None:
    w, conditions = _with_conditions(monkeypatch)
    _create(w)

    res = w.client.post("/api/marketing/coupons", json={**_BODY, "first_purchase_only": True})

    assert res.status_code == 409
    assert conditions.get("AMOR27").first_purchase_only is False


def test_if_the_condition_cannot_be_saved_nothing_is_created_in_medusa(monkeypatch) -> None:
    w, _ = _with_conditions(monkeypatch)

    class _Down(FakeCouponConditionsStore):
        def put(self, conditions: CouponConditions) -> CouponConditions:
            raise OSError("disco lleno")

    monkeypatch.setattr(coupons_api, "conditions_store", _Down)

    res = w.client.post("/api/marketing/coupons", json=_WELCOME)

    assert res.status_code == 503
    assert await_(w.admin.list_coupons()) == []


def test_the_list_and_the_detail_say_which_coupons_are_for_first_purchases(monkeypatch) -> None:
    w, _ = _with_conditions(monkeypatch)
    welcome = w.client.post("/api/marketing/coupons", json=_WELCOME).json()
    _create(w)

    listed = {c["code"]: c["first_purchase_only"] for c in w.client.get("/api/marketing/coupons").json()["coupons"]}
    detail = w.client.get(f"/api/marketing/coupons/{welcome['promotion_id']}").json()

    assert listed == {"BIENVENIDA": True, "AMOR27": False}
    assert detail["coupon"]["first_purchase_only"] is True


def test_an_unreadable_condition_is_shown_as_unknown_not_as_false(monkeypatch) -> None:
    w, _ = _with_conditions(monkeypatch)
    _create(w)

    class _Broken(FakeCouponConditionsStore):
        def get(self, code: str) -> CouponConditions:
            raise CouponConditionsError("ilegible")

    monkeypatch.setattr(coupons_api, "conditions_store", _Broken)

    [coupon] = w.client.get("/api/marketing/coupons").json()["coupons"]
    assert coupon["first_purchase_only"] is None


def test_the_condition_can_be_turned_on_and_off_and_is_audited(monkeypatch) -> None:
    w, conditions = _with_conditions(monkeypatch)
    created = _create(w)

    on = w.client.patch(f"/api/marketing/coupons/{created['promotion_id']}", json={"first_purchase_only": True})
    off = w.client.patch(f"/api/marketing/coupons/{created['promotion_id']}", json={"first_purchase_only": False})

    assert on.status_code == 200 and on.json()["first_purchase_only"] is True
    assert off.status_code == 200 and off.json()["first_purchase_only"] is False
    assert conditions.get("AMOR27").first_purchase_only is False
    # El registro va del más nuevo al más viejo.
    changes = [e.detail for e in w.audit.entries() if e.action == "conditions"]
    assert changes == [{"first_purchase_only": [True, False]}, {"first_purchase_only": [False, True]}]


def test_the_condition_can_be_set_on_a_coupon_that_is_read_only_in_medusa(monkeypatch) -> None:
    # Como el cupo: la condición es de Hubara, no de Medusa.
    w, conditions = _with_conditions(monkeypatch, seed=[_tagged_amor26()])

    res = w.client.patch("/api/marketing/coupons/promo_amor26", json={"first_purchase_only": True})

    assert res.status_code == 200, res.text
    assert conditions.get("AMOR26").first_purchase_only is True


def test_renaming_a_draft_keeps_its_condition(monkeypatch) -> None:
    w, conditions = _with_conditions(monkeypatch)
    draft = w.client.post("/api/marketing/coupons", json={**_WELCOME, "status": "draft"}).json()

    res = w.client.patch(f"/api/marketing/coupons/{draft['promotion_id']}", json={"code": "BIENVENIDO"})

    assert res.status_code == 200, res.text
    assert res.json()["first_purchase_only"] is True
    assert conditions.get("BIENVENIDO").first_purchase_only is True
    assert conditions.get("BIENVENIDA").first_purchase_only is False


def test_deleting_a_coupon_removes_its_condition(monkeypatch) -> None:
    w, conditions = _with_conditions(monkeypatch)
    draft = w.client.post("/api/marketing/coupons", json={**_WELCOME, "status": "draft"}).json()

    res = w.client.delete(f"/api/marketing/coupons/{draft['promotion_id']}")

    assert res.status_code in (200, 204), res.text
    assert conditions.get("BIENVENIDA").first_purchase_only is False
