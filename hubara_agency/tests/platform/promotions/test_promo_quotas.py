"""Cupo por unidad de un cupón (Fase 3 de CUPONES_PLAN.md) — dominio puro.

Un cupo es "producto + color + aroma + unidades". Las vendidas se derivan de
los pedidos (Fase 4); acá: cuántas quedan, el reparto de un pedido y la
validación de una fila.
"""
from __future__ import annotations

import pytest

from src.platform.promotions.quotas import (
    REASON_QUOTA_EXHAUSTED,
    PromoUnitQuota,
    QuotaGrant,
    QuotaLine,
    QuotaProduct,
    allocate_units,
    quota_id_for,
    validate_quota_rows,
    quota_exhausted,
    quota_statuses,
    unit_discount_cop,
)


def _quota(qid: str = "q_rosado_cafe", *, units: int = 5, color: str | None = "Rosado",
           aroma: str | None = "Café", product_id: str = "prod_cubo") -> PromoUnitQuota:
    return PromoUnitQuota(
        id=qid,
        promotion_id="promo_amor26",
        code="AMOR26",
        product_id=product_id,
        handle="cubo-love",
        title="Cubo Love",
        color=color,
        aroma=aroma,
        units=units,
    )


def test_units_left_is_units_minus_sold_never_negative() -> None:
    statuses = quota_statuses(
        [_quota("q1", units=5), _quota("q2", units=2, color="Azul")],
        sold={"q1": 2, "q2": 3},
    )

    assert [(s.quota.id, s.sold, s.units_left, s.oversold) for s in statuses] == [
        ("q1", 2, 3, False),
        # El operador bajó las unidades por debajo de lo vendido: 0, con aviso.
        ("q2", 3, 0, True),
    ]


def _left(*quotas: PromoUnitQuota, sold: dict[str, int] | None = None):
    return quota_statuses(list(quotas), sold=sold or {})


_PRICE = 21000  # 10% = 2.100 por unidad


def test_allocation_discounts_only_matching_product_color_aroma() -> None:
    lines = [
        QuotaLine("prod_cubo", 1, _PRICE, color="Rosado", aroma="Café"),
        QuotaLine("prod_cubo", 1, _PRICE, color="Azul", aroma="Lavanda"),
        QuotaLine("prod_otro", 1, _PRICE, color="Rosado", aroma="Café"),
    ]

    alloc = allocate_units(_left(_quota(units=5)), lines, percentage=10)

    assert alloc.grants == (QuotaGrant(line=0, quota_id="q_rosado_cafe", units=1, discount_unit_cop=2100),)
    assert alloc.discount_cop == 2100


def test_quota_key_ignores_accents_and_case() -> None:
    lines = [QuotaLine("prod_cubo", 2, _PRICE, color="  rosado ", aroma="CAFE")]

    alloc = allocate_units(_left(_quota(units=5)), lines, percentage=10)

    assert [g.units for g in alloc.grants] == [2]


def test_allocation_is_partial_when_asking_more_than_left() -> None:
    # D2 (a): quedan 1; el cliente pide 2 → 1 con descuento, 1 a precio normal.
    lines = [QuotaLine("prod_cubo", 2, _PRICE, color="Rosado", aroma="Café")]

    alloc = allocate_units(_left(_quota(units=5), sold={"q_rosado_cafe": 4}), lines, percentage=10)

    assert alloc.grants == (QuotaGrant(0, "q_rosado_cafe", 1, 2100),)
    assert alloc.units_on_line(0) == 1


def test_two_lines_same_combination_share_units_left() -> None:
    lines = [
        QuotaLine("prod_cubo", 2, _PRICE, color="Rosado", aroma="Café"),
        QuotaLine("prod_cubo", 2, _PRICE, color="rosado", aroma="cafe"),
    ]

    alloc = allocate_units(_left(_quota(units=3)), lines, percentage=10)

    assert [(g.line, g.units) for g in alloc.grants] == [(0, 2), (1, 1)]


def test_line_without_required_attributes_gets_no_quota_discount() -> None:
    # Falla cerrada: el cupo exige aroma y la línea no lo dice.
    lines = [QuotaLine("prod_cubo", 1, _PRICE, color="Rosado", aroma=None)]

    alloc = allocate_units(_left(_quota(units=5)), lines, percentage=10)

    assert alloc.grants == ()
    assert alloc.missing_attributes == (0,)


def test_quota_without_attribute_matches_any_value_of_it() -> None:
    # Producto sin lista de aromas: el cupo lleva aroma None y no lo exige.
    lines = [QuotaLine("prod_cubo", 1, _PRICE, color="Rosado", aroma=None)]

    alloc = allocate_units(_left(_quota(units=5, aroma=None)), lines, percentage=10)

    assert [g.units for g in alloc.grants] == [1]
    assert alloc.missing_attributes == ()


def test_line_of_a_product_without_quota_is_not_missing_attributes() -> None:
    lines = [QuotaLine("prod_otro", 1, _PRICE)]

    alloc = allocate_units(_left(_quota()), lines, percentage=10)

    assert alloc.grants == () and alloc.missing_attributes == ()


def test_discount_is_rounded_per_unit_in_whole_pesos() -> None:
    # Mismo redondeo que la Fase 0: la mitad sube (20.985 × 10% = 2.098,5 → 2.099),
    # y nunca más que el precio.
    assert unit_discount_cop(20985, 10) == 2099
    assert unit_discount_cop(20984, 10) == 2098
    assert unit_discount_cop(5000, 100) == 5000

    lines = [QuotaLine("prod_cubo", 3, 20985, color="Rosado", aroma="Café")]
    alloc = allocate_units(_left(_quota(units=5)), lines, percentage=10)
    assert alloc.discount_cop == 3 * 2099


def test_resolve_coupon_reports_quota_exhausted() -> None:
    exhausted = _left(_quota("q1", units=2), _quota("q2", units=1, color="Azul"), sold={"q1": 2, "q2": 1})
    some_left = _left(_quota("q1", units=2), sold={"q1": 1})

    assert quota_exhausted(exhausted) == REASON_QUOTA_EXHAUSTED
    assert quota_exhausted(some_left) is None
    # Sin filas de cupo el cupón aplica como hoy: no está "agotado".
    assert quota_exhausted([]) is None


# ---------------------------------------------------------------------------
# Validación de las filas que guarda el operador.
# ---------------------------------------------------------------------------

_CUBO = QuotaProduct(
    product_id="prod_cubo", handle="cubo-love", title="Cubo Love",
    colors=["Rosado", "Azul"], aromas=["Café", "Lavanda"],
)
_SIN_AROMA = QuotaProduct(
    product_id="prod_vaso", handle="vaso", title="Vaso", colors=["Blanco"], aromas=[],
)


def _validate(rows, *, coupon_products=("prod_cubo", "prod_vaso")):
    return validate_quota_rows(
        rows,
        promotion_id="promo_amor26",
        code="AMOR26",
        coupon_products=coupon_products,
        products={"prod_cubo": _CUBO, "prod_vaso": _SIN_AROMA},
        actor="ana",
        now_iso="2026-09-23T17:00:00Z",
    )


def test_valid_row_takes_canonical_labels_and_a_stable_id() -> None:
    quotas, errors = _validate([{"product_id": "prod_cubo", "color": "rosado", "aroma": "cafe", "units": 5}])

    assert errors == []
    assert quotas == [
        PromoUnitQuota(
            id=quota_id_for("promo_amor26", "prod_cubo", "Rosado", "Café"),
            promotion_id="promo_amor26", code="AMOR26", product_id="prod_cubo",
            handle="cubo-love", title="Cubo Love", color="Rosado", aroma="Café",
            units=5, created_at="2026-09-23T17:00:00Z", created_by="ana",
        )
    ]
    # El id es el de la combinación: guardar de nuevo NO pierde las vendidas.
    assert quota_id_for("promo_amor26", "prod_cubo", "ROSADO", "Cafe") == quotas[0].id


def test_color_not_in_product_tags_is_rejected_per_row() -> None:
    _, errors = _validate([
        {"product_id": "prod_cubo", "color": "Rosado", "aroma": "Café", "units": 5},
        {"product_id": "prod_cubo", "color": "Verde", "aroma": "Café", "units": 2},
    ])

    assert [(e.row, e.field) for e in errors] == [(1, "color")]
    assert "Verde" in errors[0].message


def test_quota_attribute_is_optional_when_product_has_no_list() -> None:
    quotas, errors = _validate([{"product_id": "prod_vaso", "color": "Blanco", "aroma": None, "units": 3}])
    _, with_aroma = _validate([{"product_id": "prod_vaso", "color": "Blanco", "aroma": "Café", "units": 3}])
    _, no_color = _validate([{"product_id": "prod_cubo", "color": None, "aroma": "Café", "units": 3}])

    assert errors == [] and quotas[0].aroma is None
    assert [(e.field, "no tiene aromas" in e.message) for e in with_aroma] == [("aroma", True)]
    assert [e.field for e in no_color] == ["color"]


def test_product_outside_coupon_is_rejected() -> None:
    _, errors = _validate(
        [{"product_id": "prod_vaso", "color": "Blanco", "aroma": None, "units": 1}],
        coupon_products=("prod_cubo",),
    )
    _, unknown = _validate([{"product_id": "prod_x", "units": 1}], coupon_products=None)

    assert [(e.row, e.field) for e in errors] == [(0, "product_id")]
    assert "no está en el cupón" in errors[0].message
    assert [e.field for e in unknown] == ["product_id"]


@pytest.mark.parametrize("units", [0, -1, 2.5, "3", None, True])
def test_units_must_be_a_positive_integer(units) -> None:
    _, errors = _validate([{"product_id": "prod_cubo", "color": "Rosado", "aroma": "Café", "units": units}])

    assert [e.field for e in errors] == ["units"]


def test_same_combination_twice_is_rejected() -> None:
    _, errors = _validate([
        {"product_id": "prod_cubo", "color": "Rosado", "aroma": "Café", "units": 5},
        {"product_id": "prod_cubo", "color": "rosado", "aroma": "CAFE", "units": 1},
    ])

    assert [(e.row, e.field) for e in errors] == [(1, "product_id")]
    assert "repetida" in errors[0].message
