"""Cupones de Medusa — reglas puras (resolución del código + cálculo del
descuento) y mapeo desde la Admin API v2 de promociones.

El bot de ventas JAMÁS inventa un descuento: el código lo valida el sistema
contra las promociones que el operador cargó en Medusa (Admin → Promotions),
y el monto lo calcula el sistema con los precios del catálogo.
"""
from __future__ import annotations

from dataclasses import replace

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
    LineDiscount,
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


def _item(
    handle, qty=1, price=40_000, product_id=None, variant_id=None, collection_id=None, tags=()
):
    return DiscountLineItem(
        handle=handle,
        quantity=qty,
        unit_price_cop=price,
        product_id=product_id or f"prod_{handle}",
        variant_id=variant_id or f"var_{handle}",
        collection_id=collection_id,
        tags=tuple(tags),
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


def test_resolve_coupon_rejects_a_shipping_coupon() -> None:
    """Decisión del operador (2026-09-23): el envío lo cobra la transportadora a
    su tarifa real y no lleva descuentos. Un cupón de envío NO se aplica: el
    bot no puede prometer un descuento que nadie honra al despachar."""
    envio = _promo(code="ENVIOGRATIS", value=100, target_type="shipping_methods")
    res = resolve_coupon("enviogratis", [envio], now_ms=_NOW)
    assert (res.ok, res.reason) == (False, "shipping_not_supported")


# --- compute_discount -------------------------------------------------------


def test_porcentaje_sobre_todo_el_pedido_redondea_a_pesos() -> None:
    res = compute_discount(_promo(value=15), [_item("a", 1, 49_500), _item("b", 2, 17_000)])
    # 15% de 83.500 = 12.525
    assert res.discount_cop == 12_525
    assert res.applicable_handles == ["a", "b"]
    assert res.reason is None


def test_percentage_discount_is_allocated_per_unit_and_sums_to_total() -> None:
    """El descuento se reparte por unidad, en pesos enteros, y suma exacto lo
    que el bot confirma: 15% de $19.990 = $2.998,5 → $2.999 por unidad. Es lo
    que el pedido escribe en Medusa como precio de cada línea (pedido #44)."""
    res = compute_discount(_promo(value=15), [_item("a", 1, 19_990), _item("b", 3, 17_000)])
    assert res.line_discounts == (
        LineDiscount(index=0, units=1, discount_unit_cop=2_999),
        LineDiscount(index=1, units=3, discount_unit_cop=2_550),
    )
    assert res.discount_cop == 10_649
    assert res.discount_cop == sum(d.units * d.discount_unit_cop for d in res.line_discounts)


def test_percentage_each_respects_max_quantity_per_line() -> None:
    """AMOR26 es 10% `each` con tope de 10 unidades POR LÍNEA, como en Medusa
    (`max_quantity` de `each` no es un tope del pedido): de 12 Cubo Love se
    descuentan 10; las 3 unidades de otra línea llevan su propio cupo."""
    promo = _promo(value=10, allocation="each", max_quantity=10)
    res = compute_discount(promo, [_item("a", 12, 21_000), _item("b", 3, 20_000)])
    assert res.line_discounts == (
        LineDiscount(index=0, units=10, discount_unit_cop=2_100),
        LineDiscount(index=1, units=3, discount_unit_cop=2_000),
    )
    assert res.discount_cop == 27_000


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


def test_once_allocation_discounts_the_cheapest_units_up_to_max_quantity_per_order() -> None:
    """`once` (panel de Medusa 2.12: "aplica a un número limitado de ítems"):
    `max_quantity` es el tope del PEDIDO y se llena con las unidades más
    baratas primero. 10% a 3 unidades: las 2 de $20.000 y 1 de $21.000; el
    resto va a precio de lista."""
    promo = _promo(value=10, allocation="once", max_quantity=3)
    res = compute_discount(
        promo, [_item("a", 1, 30_000), _item("b", 2, 21_000), _item("c", 2, 20_000)]
    )
    assert res.line_discounts == (
        LineDiscount(index=1, units=1, discount_unit_cop=2_100),
        LineDiscount(index=2, units=2, discount_unit_cop=2_000),
    )
    assert res.discount_cop == 6_100


def test_once_without_max_quantity_is_unsupported_not_a_zero_discount() -> None:
    """Medusa exige `max_quantity` en `once`: un snapshot sin tope no se
    entiende. La regla no dice "aplicó por $0": dice que no lo soporta."""
    promo = _promo(value=10, allocation="once", max_quantity=None)
    res = compute_discount(promo, [_item("a", 2, 20_000)])
    assert (res.discount_cop, res.reason, res.line_discounts) == (0, "unsupported", ())


def test_fixed_order_discount_is_prorated_with_remainder_on_last_line() -> None:
    """$5.000 al pedido se reparten según el subtotal de cada línea, por unidad
    y en pesos enteros, sin perder pesos: cada unidad lleva la parte entera y
    lo que sobra va a la última línea. Si ahí no se divide exacto entre sus
    unidades, la línea se parte en dos tramos que difieren en $1."""
    promo = _promo(discount_type="fixed", value=5_000, target_type="order", allocation="across")
    res = compute_discount(promo, [_item("a", 2, 10_000), _item("b", 1, 10_000)])
    assert res.line_discounts == (
        LineDiscount(index=0, units=2, discount_unit_cop=1_666),
        LineDiscount(index=1, units=1, discount_unit_cop=1_668),
    )
    assert res.discount_cop == 5_000

    promo = replace(promo, value=5_001)
    res = compute_discount(promo, [_item("a", 1, 10_000), _item("b", 3, 10_000)])
    assert res.line_discounts == (
        LineDiscount(index=0, units=1, discount_unit_cop=1_250),
        LineDiscount(index=1, units=2, discount_unit_cop=1_250),
        LineDiscount(index=1, units=1, discount_unit_cop=1_251),
    )
    assert res.discount_cop == 5_001


def test_fixed_order_remainder_that_does_not_fit_on_the_last_line_goes_to_the_previous_one() -> None:
    """Una unidad nunca baja de $0: si los pesos que sobran no caben en la
    última línea, pasan a la anterior (que se parte en tramos de $1 de
    diferencia). La suma sigue siendo exacta."""
    promo = _promo(discount_type="fixed", value=21, target_type="order", allocation="across")
    res = compute_discount(promo, [_item("a", 3, 7), _item("b", 1, 1)])
    assert res.line_discounts == (
        LineDiscount(index=0, units=1, discount_unit_cop=6),
        LineDiscount(index=0, units=2, discount_unit_cop=7),
        LineDiscount(index=1, units=1, discount_unit_cop=1),
    )
    assert res.discount_cop == 21


def test_fixed_each_is_allocated_per_unit_with_max_quantity_per_line() -> None:
    """$5.000 por unidad, hasta 2 por línea (como en Medusa): la segunda línea
    tiene su propio tope, y una unidad de $4.000 no baja de $0."""
    promo = _promo(discount_type="fixed", value=5_000, allocation="each", max_quantity=2)
    res = compute_discount(promo, [_item("a", 3, 30_000), _item("b", 2, 4_000)])
    assert res.line_discounts == (
        LineDiscount(index=0, units=2, discount_unit_cop=5_000),
        LineDiscount(index=1, units=2, discount_unit_cop=4_000),
    )
    assert res.discount_cop == 18_000


def test_minimo_de_compra_no_alcanzado() -> None:
    promo = _promo(value=10, min_subtotal_cop=100_000)
    res = compute_discount(promo, [_item("a", 1, 40_000)])
    assert res.discount_cop == 0
    assert res.reason == "min_subtotal"
    assert res.min_subtotal_cop == 100_000


def test_un_cupon_de_envio_no_descuenta_ni_el_envio_ni_los_productos() -> None:
    """El envío se cobra a la tarifa real de la transportadora (decisión del
    operador, 2026-09-23). Aunque un snapshot de cupón de envío ya esté
    guardado en un episodio, no descuenta nada."""
    promo = _promo(discount_type="fixed", value=20_000, target_type="shipping_methods")
    res = compute_discount(promo, [_item("a", 1, 40_000)], shipping_cop=7_900)
    assert (res.discount_cop, res.reason, res.line_discounts) == (0, "shipping_not_supported", ())


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



# --- Alcance de la promoción: la lista de productos tiene que llegar ----------
# Incidente 2026-09-22 (cupón AMOR26): Medusa devolvió la regla
# `items.product.id in [...]` SIN `values` porque no los pedíamos en `fields`
# → product_ids vacío → el bot dijo "10% en todo el catálogo".


def test_promotion_fields_pide_los_valores_de_las_reglas() -> None:
    from src.platform.medusa.client import HttpMedusaClient

    fields = HttpMedusaClient.PROMOTION_FIELDS
    assert "*application_method.target_rules.values" in fields
    assert "*rules.values" in fields


def test_regla_de_productos_sin_valores_es_alcance_desconocido_no_todo_el_catalogo() -> None:
    raw = _raw()
    raw["application_method"]["target_rules"] = [
        {"attribute": "items.product.id", "operator": "in"}  # sin values
    ]
    p = promotion_from_medusa(raw)
    assert p is not None
    assert p.product_ids == ()
    assert p.scope_unresolved is True


def test_minimo_de_compra_sin_valores_tambien_es_alcance_desconocido() -> None:
    raw = _raw()
    raw["rules"] = [{"attribute": "item_total", "operator": "gte"}]
    p = promotion_from_medusa(raw)
    assert p is not None and p.scope_unresolved is True


def test_promocion_completa_o_sin_reglas_no_es_desconocida() -> None:
    assert promotion_from_medusa(_raw()).scope_unresolved is False
    raw = _raw()
    raw["application_method"]["target_rules"] = []
    raw["rules"] = []
    assert promotion_from_medusa(raw).scope_unresolved is False


def test_resolve_coupon_con_alcance_desconocido_no_aplica() -> None:
    raw = _raw()
    raw["application_method"]["target_rules"] = [{"attribute": "items.product.id", "operator": "in"}]
    promo = promotion_from_medusa(raw)
    res = resolve_coupon("MAMA15", [promo], now_ms=1_778_000_000_000)
    assert res.ok is False
    assert res.reason == "scope_unresolved"


# --- Condición por etiquetas de producto --------------------------------------
# Run 28a8e407 (2026-09-23, 15:26): a AMOR26 le agregaron en Medusa la regla
# `items.product.tags.id in [...]`. El lector no la entendía → alcance
# desconocido → el bot rechazó el cupón a todos y lo escondió de la lista.


def _raw_with_tags() -> dict:
    raw = _raw()
    raw["application_method"]["target_rules"] = [
        {
            "attribute": "items.product.id",
            "operator": "in",
            "values": [{"value": "prod_a"}, {"value": "prod_b"}],
        },
        {
            "attribute": "items.product.tags.id",
            "operator": "in",
            "values": [{"value": "ptag_rosado"}, {"value": "ptag_cafe"}],
        },
    ]
    return raw


_TAG_NAMES = {"ptag_rosado": "Color: Rosado", "ptag_cafe": "Aroma: Café"}


def test_regla_por_etiquetas_se_lee_con_el_nombre_de_cada_etiqueta() -> None:
    p = promotion_from_medusa(_raw_with_tags(), tag_values=_TAG_NAMES)
    assert p is not None
    assert p.tag_values == ("Color: Rosado", "Aroma: Café")
    assert p.product_ids == ("prod_a", "prod_b")
    assert p.scope_unresolved is False


def test_etiqueta_sin_nombre_conocido_sigue_siendo_alcance_desconocido() -> None:
    assert promotion_from_medusa(_raw_with_tags()).scope_unresolved is True
    partial = {"ptag_rosado": "Color: Rosado"}
    assert promotion_from_medusa(_raw_with_tags(), tag_values=partial).scope_unresolved is True


def test_productos_y_etiquetas_se_cumplen_las_dos_como_en_medusa() -> None:
    promo = _promo(product_ids=("prod_a", "prod_b"), tag_values=("Color: Rosado",))
    res = compute_discount(
        promo,
        [
            _item("a", product_id="prod_a", tags=("Color: Rosado", "Aroma: Café")),
            _item("b", product_id="prod_b", tags=("Color: Azul",)),
            _item("c", product_id="prod_c", tags=("Color: Rosado",)),
        ],
    )
    assert res.applicable_handles == ["a"]
    assert res.discount_cop == 6_000


def test_promo_solo_por_etiquetas_selecciona_los_productos_que_las_tienen() -> None:
    promo = _promo(tag_values=("Aroma: Café",))
    res = compute_discount(promo, [_item("a", tags=("Aroma: Café",)), _item("b")])
    assert res.applicable_handles == ["a"]
