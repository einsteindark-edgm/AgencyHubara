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
