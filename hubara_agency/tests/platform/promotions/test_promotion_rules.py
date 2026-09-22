"""Cupones de Medusa — reglas puras (resolución del código + cálculo del
descuento) y mapeo desde la Admin API v2 de promociones.

El bot de ventas JAMÁS inventa un descuento: el código lo valida el sistema
contra las promociones que el operador cargó en Medusa (Admin → Promotions),
y el monto lo calcula el sistema con los precios del catálogo.
"""
from __future__ import annotations

import pytest

from src.platform.promotions.medusa import promotion_from_medusa
from src.platform.promotions.port import (
    DiscountLineItem,
    FakePromotionsPort,
    NullPromotionsPort,
    PromotionDTO,
)
from src.platform.promotions.rules import (
    COUPON_CODE_RE,
    compute_discount,
    resolve_coupon,
)

_DAY = 24 * 60 * 60 * 1000
_NOW = 1_750_000_000_000


def _promo(**over) -> PromotionDTO:
    base = dict(
        id="promo_1",
        code="MAMA15",
        discount_type="percentage",
        value=15,
        currency_code="cop",
        target_type="items",
        allocation="across",
        max_quantity=None,
        product_ids=(),
        variant_ids=(),
        collection_ids=(),
        min_subtotal_cop=None,
        is_automatic=False,
        status="active",
        starts_at_ms=None,
        ends_at_ms=None,
        budget_type=None,
        budget_limit=None,
        budget_used=None,
        description=None,
    )
    base.update(over)
    return PromotionDTO(**base)


def _item(handle, qty=1, price=40_000, product_id=None, variant_id=None, collection_id=None):
    return DiscountLineItem(
        handle=handle,
        quantity=qty,
        unit_price_cop=price,
        product_id=product_id or f"prod_{handle}",
        variant_id=variant_id or f"var_{handle}",
        collection_id=collection_id,
    )


# --- formato del código -----------------------------------------------------


def test_coupon_code_regex_rechaza_forma_de_tag_interno() -> None:
    # `VELAS_10` colisiona con el patrón de tag interno del guard de egreso
    # (memoria coupon-tag-shape-collision): el bot enmudecería.
    assert COUPON_CODE_RE.fullmatch("MAMA15")
    assert COUPON_CODE_RE.fullmatch("PAPA20")
    assert not COUPON_CODE_RE.fullmatch("VELAS_10")
    assert not COUPON_CODE_RE.fullmatch("mama-15")
    assert not COUPON_CODE_RE.fullmatch("AB")  # demasiado corto
    assert not COUPON_CODE_RE.fullmatch("A" * 21)


# --- resolve_coupon ---------------------------------------------------------


def test_resolve_coupon_encuentra_por_codigo_sin_importar_mayusculas() -> None:
    res = resolve_coupon(" mama15 ", [_promo()], now_ms=_NOW)
    assert res.ok is True
    assert res.promotion is not None and res.promotion.code == "MAMA15"
    assert res.reason is None


def test_resolve_coupon_razones_de_rechazo() -> None:
    assert resolve_coupon("VELAS_10", [_promo()], now_ms=_NOW).reason == "invalid_format"
    assert resolve_coupon("NOEXISTE", [_promo()], now_ms=_NOW).reason == "not_found"
    assert (
        resolve_coupon("MAMA15", [_promo(status="inactive")], now_ms=_NOW).reason
        == "inactive"
    )
    assert (
        resolve_coupon("MAMA15", [_promo(starts_at_ms=_NOW + _DAY)], now_ms=_NOW).reason
        == "not_started"
    )
    assert (
        resolve_coupon("MAMA15", [_promo(ends_at_ms=_NOW - 1)], now_ms=_NOW).reason
        == "expired"
    )
    assert (
        resolve_coupon(
            "MAMA15",
            [_promo(budget_type="usage", budget_limit=10, budget_used=10)],
            now_ms=_NOW,
        ).reason
        == "budget_exhausted"
    )
    # Una promo automática (sin código) no se aplica "a mano" por código.
    assert (
        resolve_coupon("MAMA15", [_promo(is_automatic=True)], now_ms=_NOW).reason
        == "not_found"
    )


# --- compute_discount -------------------------------------------------------


def test_porcentaje_sobre_todo_el_pedido_redondea_a_pesos() -> None:
    res = compute_discount(_promo(value=15), [_item("a", 1, 49_500), _item("b", 2, 17_000)])
    # 15% de 83.500 = 12.525
    assert res.discount_cop == 12_525
    assert res.applicable_handles == ["a", "b"]
    assert res.reason is None


def test_porcentaje_solo_sobre_los_productos_de_la_promo() -> None:
    promo = _promo(value=10, product_ids=("prod_a",))
    res = compute_discount(promo, [_item("a", 1, 40_000), _item("b", 1, 30_000)])
    assert res.discount_cop == 4_000
    assert res.applicable_handles == ["a"]


def test_variante_y_coleccion_tambien_seleccionan() -> None:
    promo = _promo(value=50, variant_ids=("var_b",))
    res = compute_discount(promo, [_item("a"), _item("b", 1, 10_000)])
    assert (res.discount_cop, res.applicable_handles) == (5_000, ["b"])
    promo = _promo(value=50, collection_ids=("col_x",))
    res = compute_discount(
        promo, [_item("a", collection_id="col_x", price=10_000), _item("b")]
    )
    assert (res.discount_cop, res.applicable_handles) == (5_000, ["a"])


def test_sin_productos_aplicables_no_hay_descuento() -> None:
    promo = _promo(value=10, product_ids=("prod_zzz",))
    res = compute_discount(promo, [_item("a")])
    assert res.discount_cop == 0
    assert res.reason == "no_applicable_items"


def test_fijo_across_no_supera_el_subtotal_aplicable() -> None:
    promo = _promo(discount_type="fixed", value=50_000, allocation="across")
    res = compute_discount(promo, [_item("a", 1, 30_000)])
    assert res.discount_cop == 30_000


def test_fijo_each_por_unidad_con_tope_max_quantity() -> None:
    promo = _promo(discount_type="fixed", value=5_000, allocation="each", max_quantity=2)
    res = compute_discount(promo, [_item("a", 3, 30_000)])
    assert res.discount_cop == 10_000


def test_minimo_de_compra_no_alcanzado() -> None:
    promo = _promo(value=10, min_subtotal_cop=100_000)
    res = compute_discount(promo, [_item("a", 1, 40_000)])
    assert res.discount_cop == 0
    assert res.reason == "min_subtotal"
    assert res.min_subtotal_cop == 100_000


def test_descuento_de_envio_aplica_al_envio_no_a_los_productos() -> None:
    promo = _promo(discount_type="fixed", value=20_000, target_type="shipping_methods")
    res = compute_discount(promo, [_item("a", 1, 40_000)], shipping_cop=7_900)
    assert res.discount_cop == 7_900
    assert res.applies_to_shipping is True
    assert res.applicable_handles == []


def test_promocion_buyget_no_se_calcula_aca() -> None:
    promo = _promo(discount_type="buyget")
    res = compute_discount(promo, [_item("a")])
    assert res.discount_cop == 0
    assert res.reason == "unsupported"


# --- mapper Medusa v2 -------------------------------------------------------


def _raw(**over) -> dict:
    raw = {
        "id": "promo_01",
        "code": "MAMA15",
        "type": "standard",
        "is_automatic": False,
        "status": "active",
        "application_method": {
            "type": "percentage",
            "value": 15,
            "currency_code": "cop",
            "target_type": "items",
            "allocation": "across",
            "max_quantity": None,
            "target_rules": [
                {
                    "attribute": "items.product.id",
                    "operator": "in",
                    "values": [{"id": "pv_1", "value": "prod_a"}, {"id": "pv_2", "value": "prod_b"}],
                },
                {"attribute": "items.variant.id", "operator": "eq", "values": ["var_z"]},
                {"attribute": "items.product.collection_id", "operator": "in", "values": [{"value": "col_x"}]},
            ],
        },
        "rules": [
            {"attribute": "item_total", "operator": "gte", "values": [{"value": "60000"}]},
        ],
        "campaign": {
            "name": "Madres",
            "starts_at": "2026-05-01T00:00:00.000Z",
            "ends_at": "2026-05-31T23:59:59.000Z",
            "budget": {"type": "usage", "limit": 100, "used": 12, "currency_code": None},
        },
    }
    raw.update(over)
    return raw


def test_promotion_from_medusa_mapea_reglas_fechas_y_presupuesto() -> None:
    p = promotion_from_medusa(_raw())
    assert p is not None
    assert p.code == "MAMA15"
    assert p.discount_type == "percentage" and p.value == 15
    assert p.product_ids == ("prod_a", "prod_b")
    assert p.variant_ids == ("var_z",)
    assert p.collection_ids == ("col_x",)
    assert p.min_subtotal_cop == 60_000
    assert p.starts_at_ms == 1_777_593_600_000
    assert p.ends_at_ms == 1_780_271_999_000
    assert (p.budget_type, p.budget_limit, p.budget_used) == ("usage", 100, 12)
    assert p.description == "Madres"


def test_promotion_from_medusa_tolera_shape_viejo_sin_status_ni_campaign() -> None:
    raw = _raw()
    raw.pop("status")
    raw.pop("campaign")
    p = promotion_from_medusa(raw)
    assert p is not None
    assert p.status == "active"
    assert p.starts_at_ms is None and p.budget_limit is None


def test_promotion_from_medusa_descarta_sin_codigo_o_sin_metodo() -> None:
    assert promotion_from_medusa(_raw(code=None)) is None
    assert promotion_from_medusa(_raw(application_method=None)) is None


# --- ports ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_y_null_ports() -> None:
    fake = FakePromotionsPort([_promo(), _promo(id="p2", code="PAPA20", status="inactive")])
    assert [p.code for p in await fake.list_active()] == ["MAMA15"]
    assert (await fake.get_by_code("papa20")) is not None
    assert (await fake.get_by_code("NADA")) is None
    null = NullPromotionsPort()
    assert await null.list_active() == []
    assert await null.get_by_code("MAMA15") is None
