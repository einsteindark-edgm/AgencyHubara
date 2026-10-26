"""Central de cupones: «solo quien lo recibió» (reseñas tras la entrega, 2026-10-13).

GRACIAS5 / GRACIAS10 son cupones personales: el plugin de reseñas los concede
a quien califica su pedido y solo esa persona los usa, una vez. La central los
crea con la condición `granted_only` (en el vault, ANTES de Medusa, como la de
primera compra) y no la pierde si después se toca la de primera compra.
"""
from __future__ import annotations

from tests.plugins.marketing.test_coupons_api import _BODY
from tests.plugins.marketing.test_coupons_api_first_purchase import _with_conditions

_THANKS = {**_BODY, "code": "gracias5", "campaign_name": "Gracias por calificar", "percentage": 5,
           "products": "all", "granted_only": True}


def test_a_thanks_coupon_is_created_only_for_who_receives_it(monkeypatch) -> None:
    w, conditions = _with_conditions(monkeypatch)

    res = w.client.post("/api/marketing/coupons", json=_THANKS)

    assert res.status_code == 201, res.text
    assert res.json()["granted_only"] is True
    assert conditions.get("GRACIAS5").granted_only is True
    assert conditions.get("GRACIAS5").first_purchase_only is False


def test_toggling_first_purchase_keeps_the_granted_only_condition(monkeypatch) -> None:
    w, conditions = _with_conditions(monkeypatch)
    created = w.client.post("/api/marketing/coupons", json=_THANKS).json()

    w.client.patch(f"/api/marketing/coupons/{created['promotion_id']}", json={"first_purchase_only": True})

    assert conditions.get("GRACIAS5").granted_only is True
    assert conditions.get("GRACIAS5").first_purchase_only is True


# --- Premortem C6: la condición no se pierde ni se filtra a las campañas ------


def test_renaming_a_thanks_draft_carries_its_condition(monkeypatch) -> None:
    w, conditions = _with_conditions(monkeypatch)
    draft = w.client.post("/api/marketing/coupons", json={**_THANKS, "status": "draft"}).json()

    res = w.client.patch(f"/api/marketing/coupons/{draft['promotion_id']}", json={"code": "GRACIASCINCO"})

    assert res.status_code == 200, res.text
    assert res.json()["granted_only"] is True
    assert conditions.get("GRACIASCINCO").granted_only is True
    assert conditions.get("GRACIAS5").granted_only is False


def test_turning_off_first_purchase_keeps_it_personal(monkeypatch) -> None:
    """Quitar «primera compra» borraba el archivo entero: GRACIAS10 quedaba
    público (cualquiera que escribiera el código lo usaba)."""
    w, conditions = _with_conditions(monkeypatch)
    created = w.client.post("/api/marketing/coupons", json={**_THANKS, "first_purchase_only": True}).json()

    res = w.client.patch(
        f"/api/marketing/coupons/{created['promotion_id']}", json={"percentage": 6, "first_purchase_only": False}
    )

    assert res.status_code == 200, res.text
    assert conditions.get("GRACIAS5").granted_only is True
    assert conditions.get("GRACIAS5").first_purchase_only is False


def test_granted_only_can_be_turned_on_and_off_and_is_audited(monkeypatch) -> None:
    from tests.plugins.marketing.test_coupons_api import _create

    w, conditions = _with_conditions(monkeypatch)
    created = _create(w)

    on = w.client.patch(f"/api/marketing/coupons/{created['promotion_id']}", json={"granted_only": True})
    assert on.status_code == 200 and on.json()["granted_only"] is True
    assert conditions.get("AMOR27").granted_only is True

    off = w.client.patch(f"/api/marketing/coupons/{created['promotion_id']}", json={"granted_only": False})
    assert off.status_code == 200 and off.json()["granted_only"] is False
    assert conditions.get("AMOR27").granted_only is False
    changes = [e.detail for e in w.audit.entries() if e.action == "conditions"]
    assert changes == [{"granted_only": [True, False]}, {"granted_only": [False, True]}]


def test_granted_only_must_be_yes_or_no(monkeypatch) -> None:
    from tests.plugins.marketing.test_coupons_api import _create

    w, _ = _with_conditions(monkeypatch)
    created = _create(w)

    res = w.client.patch(f"/api/marketing/coupons/{created['promotion_id']}", json={"granted_only": "sí"})

    assert res.status_code == 422


def test_campaigns_never_offer_a_personal_coupon(monkeypatch) -> None:
    from dataclasses import replace

    from fastapi.testclient import TestClient

    import src.plugins.marketing.api as api_mod
    from src.main import app
    from src.sdk.connectorkit import FakePromotionsPort
    from tests.plugins.marketing.test_marketing_api import _promo_dto

    personal = replace(_promo_dto("GRACIAS10"), granted_only=True)
    monkeypatch.setattr(api_mod, "get_promotions_port", lambda: FakePromotionsPort([_promo_dto("MAMA15"), personal]))

    res = TestClient(app).get("/api/marketing/promotions")

    assert [p["code"] for p in res.json()["promotions"]] == ["MAMA15"]


def test_a_campaign_cannot_announce_a_personal_coupon() -> None:
    import asyncio
    from dataclasses import replace

    from src.plugins.marketing.campaign_coupon import check_campaign_coupon
    from src.sdk.connectorkit import FakePromotionsPort
    from tests.plugins.marketing.test_marketing_api import _promo_dto

    personal = replace(_promo_dto("GRACIAS10"), granted_only=True)

    check = asyncio.run(check_campaign_coupon(
        FakePromotionsPort([personal]), {"coupon_code": "GRACIAS10"}, at_ms=1_791_590_000_000,
    ))

    assert check is not None and check.problem and "personal" in check.problem
