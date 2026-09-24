"""Dominio del cupón de la central (Fase 1 de CUPONES_PLAN.md).

`CouponSpec` es lo que llena el operador en Marketing → Cupones; el mapeo a
`POST /admin/promotions` y la lectura de vuelta (`CouponView`) son puros.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.platform.promotions.coupon import (
    CouponSpec,
    CouponSpecError,
    coupon_to_medusa_payload,
    coupon_view_from_medusa,
    parse_coupon_spec,
)


def _spec(**overrides) -> CouponSpec:
    base = dict(
        code="AMOR27",
        campaign_name="AMOR Y AMISTAD 2026",
        percentage=10,
        products=("prod_a", "prod_b"),
        starts_on=date(2026, 9, 22),
        ends_on=date(2026, 9, 27),
        status="active",
    )
    base.update(overrides)
    return CouponSpec(**base)


def test_percentage_coupon_for_selected_products_maps_to_medusa_payload() -> None:
    payload = coupon_to_medusa_payload(_spec())

    assert payload == {
        "code": "AMOR27",
        "type": "standard",
        "is_automatic": False,
        "status": "active",
        "application_method": {
            "type": "percentage",
            "value": 10,
            "target_type": "items",
            # `across` sin `max_quantity`: el tope por unidad lo pone el cupo.
            "allocation": "across",
            "target_rules": [
                {
                    "attribute": "items.product.id",
                    "operator": "in",
                    "values": ["prod_a", "prod_b"],
                }
            ],
        },
        "campaign": {
            "name": "AMOR Y AMISTAD 2026",
            "campaign_identifier": "AMOR27",
            "starts_at": "2026-09-22T05:00:00Z",
            "ends_at": "2026-09-28T05:00:00Z",
        },
    }


def test_whole_catalog_coupon_sends_no_target_rules() -> None:
    payload = coupon_to_medusa_payload(_spec(products=None))

    assert payload["application_method"]["target_rules"] == []


def test_until_date_is_inclusive_in_bogota() -> None:
    # El 31-dic cuenta completo: la campaña cierra el 1-ene a las 00:00 Bogotá.
    payload = coupon_to_medusa_payload(_spec(ends_on=date(2026, 12, 31)))

    assert payload["campaign"]["ends_at"] == "2027-01-01T05:00:00Z"


def test_from_date_starts_at_midnight_bogota() -> None:
    payload = coupon_to_medusa_payload(_spec(starts_on=date(2026, 10, 1)))

    assert payload["campaign"]["starts_at"] == "2026-10-01T05:00:00Z"


def _raw(**overrides) -> dict:
    base = {
        "code": " amor27 ",
        "campaign_name": "AMOR Y AMISTAD 2026",
        "percentage": 10,
        "products": ["prod_a"],
        "starts_on": "2026-09-22",
        "ends_on": "2026-09-27",
        "status": "active",
    }
    base.update(overrides)
    return base


def test_parse_coupon_spec_normalizes_code_and_reads_dates() -> None:
    spec = parse_coupon_spec(_raw())

    assert spec == CouponSpec(
        code="AMOR27",
        campaign_name="AMOR Y AMISTAD 2026",
        percentage=10,
        products=("prod_a",),
        starts_on=date(2026, 9, 22),
        ends_on=date(2026, 9, 27),
        status="active",
    )


@pytest.mark.parametrize("code", ["AMOR_26", "AMOR 26", "AB", "ABCDEFGHIJKLMNO", "ÑANDÚ26", ""])
def test_code_must_be_3_to_14_uppercase_letters_or_digits(code: str) -> None:
    with pytest.raises(CouponSpecError) as err:
        parse_coupon_spec(_raw(code=code))

    assert err.value.field == "code"
    assert "3 a 14" in err.value.message


@pytest.mark.parametrize("pct", [0, 101, 12.5, "diez", True, None])
def test_percentage_must_be_integer_between_1_and_100(pct) -> None:
    with pytest.raises(CouponSpecError) as err:
        parse_coupon_spec(_raw(percentage=pct))

    assert err.value.field == "percentage"
    assert "entre 1 y 100" in err.value.message


def test_until_before_from_is_rejected() -> None:
    with pytest.raises(CouponSpecError) as err:
        parse_coupon_spec(_raw(starts_on="2026-09-27", ends_on="2026-09-22"))

    assert err.value.field == "ends_on"
    assert "no puede ser antes" in err.value.message


def test_campaign_name_defaults_to_code_and_is_capped() -> None:
    assert parse_coupon_spec(_raw(campaign_name="  ")).campaign_name == "AMOR27"
    with pytest.raises(CouponSpecError) as err:
        parse_coupon_spec(_raw(campaign_name="x" * 81))
    assert err.value.field == "campaign_name"


def test_products_all_means_whole_catalog_and_empty_list_is_rejected() -> None:
    assert parse_coupon_spec(_raw(products="all")).products is None
    with pytest.raises(CouponSpecError) as err:
        parse_coupon_spec(_raw(products=[]))
    assert err.value.field == "products"


def test_new_coupon_status_is_draft_or_active() -> None:
    assert parse_coupon_spec(_raw(status="draft")).status == "draft"
    with pytest.raises(CouponSpecError) as err:
        parse_coupon_spec(_raw(status="inactive"))
    assert err.value.field == "status"


def test_bad_date_is_rejected_with_field() -> None:
    with pytest.raises(CouponSpecError) as err:
        parse_coupon_spec(_raw(starts_on="22/09/2026"))
    assert err.value.field == "starts_on"


@pytest.mark.parametrize(
    ("field", "day"),
    [("ends_on", "9999-12-31"), ("ends_on", "2101-01-01"), ("starts_on", "1999-12-31")],
)
def test_a_date_outside_2000_2100_is_a_field_error_not_an_overflow(field: str, day: str) -> None:
    """"9999-12-31" desbordaba al sumar el día del "hasta" inclusivo → 500."""
    with pytest.raises(CouponSpecError) as err:
        coupon_to_medusa_payload(parse_coupon_spec({**_raw(), field: day}))

    assert err.value.field == field
    assert "2000" in err.value.message and "2100" in err.value.message


def test_edge_years_2000_and_2100_are_accepted() -> None:
    spec = parse_coupon_spec(_raw(starts_on="2000-01-01", ends_on="2100-12-31"))

    assert coupon_to_medusa_payload(spec)["campaign"]["ends_at"] == "2101-01-01T05:00:00Z"


# --- Editar: solo se valida lo que cambia (premortem A9) -----------------------


def _current(**overrides) -> dict:
    """El formulario del cupón tal como está en Medusa."""
    return {**_raw(code="AMORYAMISTAD2026", campaign_name="x" * 90), **overrides}


def test_editing_keeps_an_unchanged_code_and_name_the_central_would_not_create() -> None:
    """Un cupón creado en Medusa con 16 caracteres (la central acepta 3–14)
    se lista como gestionable: cualquier PATCH fallaba 422 en `code` aunque
    no se tocara el código."""
    current = _current()

    spec = parse_coupon_spec({**current, "percentage": 15}, current=current)

    assert (spec.code, spec.campaign_name, spec.percentage) == ("AMORYAMISTAD2026", "x" * 90, 15)


def test_editing_still_validates_what_changes() -> None:
    current = _current()

    with pytest.raises(CouponSpecError) as code_err:
        parse_coupon_spec({**current, "code": "AMOR_27"}, current=current)
    with pytest.raises(CouponSpecError) as pct_err:
        parse_coupon_spec({**current, "percentage": 0}, current=current)
    with pytest.raises(CouponSpecError) as name_err:
        parse_coupon_spec({**current, "campaign_name": "y" * 81}, current=current)

    assert (code_err.value.field, pct_err.value.field, name_err.value.field) == (
        "code", "percentage", "campaign_name",
    )


# ---------------------------------------------------------------------------
# CouponView: lo que la central muestra de cada promoción de Medusa.
# Shape real de `GET /admin/promotions` (ids saneados).
# ---------------------------------------------------------------------------


def _medusa(**overrides) -> dict:
    raw = {
        "id": "promo_01",
        "code": "AMOR27",
        "type": "standard",
        "is_automatic": False,
        "status": "active",
        "campaign_id": "camp_01",
        "application_method": {
            "id": "proappmet_01",
            "type": "percentage",
            "value": 10,
            "target_type": "items",
            "allocation": "across",
            "max_quantity": None,
            "target_rules": [
                {
                    "id": "prorul_01",
                    "attribute": "items.product.id",
                    "operator": "in",
                    "values": [{"id": "prorulval_1", "value": "prod_a"}, {"id": "prorulval_2", "value": "prod_b"}],
                }
            ],
        },
        "rules": [],
        "campaign": {
            "id": "camp_01",
            "name": "AMOR Y AMISTAD 2026",
            "campaign_identifier": "AMOR27",
            "starts_at": "2026-09-22T05:00:00.000Z",
            "ends_at": "2026-09-28T05:00:00.000Z",
            "budget": None,
        },
    }
    raw.update(overrides)
    return raw


def _at(y, m, d, hh=12) -> datetime:
    return datetime(y, m, d, hh, tzinfo=timezone.utc)


def test_coupon_view_reads_back_the_form_fields() -> None:
    view = coupon_view_from_medusa(_medusa(), now=_at(2026, 9, 23))

    assert view.promotion_id == "promo_01"
    assert view.campaign_id == "camp_01"
    assert view.code == "AMOR27"
    assert view.campaign_name == "AMOR Y AMISTAD 2026"
    assert view.percentage == 10
    assert view.products == ("prod_a", "prod_b")
    # El "hasta" se lee inclusivo: la campaña cierra el 28 a las 00:00 Bogotá.
    assert view.starts_on == date(2026, 9, 22)
    assert view.ends_on == date(2026, 9, 27)


@pytest.mark.parametrize(
    ("status", "now", "state"),
    [
        ("draft", _at(2026, 9, 23), "draft"),
        ("active", _at(2026, 9, 21), "scheduled"),
        ("active", _at(2026, 9, 23), "active"),
        ("inactive", _at(2026, 9, 23), "paused"),
        # Vencido gana sobre cualquier estado (28-sep 00:00 Bogotá = 05:00Z).
        ("active", datetime(2026, 9, 28, 5, 0, tzinfo=timezone.utc), "expired"),
        ("inactive", _at(2026, 10, 1), "expired"),
        ("draft", _at(2026, 10, 1), "expired"),
    ],
)
def test_coupon_view_state_is_derived_from_status_and_dates(status, now, state) -> None:
    assert coupon_view_from_medusa(_medusa(status=status), now=now).state == state


def test_manageable_percentage_coupon_has_no_reason() -> None:
    view = coupon_view_from_medusa(_medusa(), now=_at(2026, 9, 23))

    assert view.manageable is True
    assert view.unmanageable_reason is None


def test_promotion_with_tag_rule_is_not_manageable() -> None:
    # AMOR26 hoy: productos + condición por etiquetas creada en Medusa Admin.
    raw = _medusa()
    raw["application_method"]["target_rules"].append(
        {"attribute": "items.product.tags.id", "operator": "in", "values": [{"value": "ptag_1"}]}
    )

    view = coupon_view_from_medusa(raw, now=_at(2026, 9, 23))

    assert view.manageable is False
    assert "etiquetas" in view.unmanageable_reason


def test_fixed_amount_promotion_is_not_manageable() -> None:
    raw = _medusa()
    raw["application_method"].update(type="fixed", value=5000, currency_code="cop")

    view = coupon_view_from_medusa(raw, now=_at(2026, 9, 23))

    assert view.manageable is False
    assert view.percentage is None
    assert "monto fijo" in view.unmanageable_reason


@pytest.mark.parametrize(
    ("mutate", "reason_fragment"),
    [
        (lambda r: r.update(is_automatic=True), "automática"),
        (lambda r: r.update(type="buyget"), "compra"),
        (lambda r: r["application_method"].update(target_type="shipping_methods"), "envío"),
        (lambda r: r["application_method"].update(target_type="order"), "pedido completo"),
        (lambda r: r.update(rules=[{"attribute": "item_total", "operator": "gte", "values": [{"value": "80000"}]}]), "condiciones de compra"),
        (lambda r: r.update(campaign=None, campaign_id=None), "campaña"),
        (lambda r: r["application_method"]["target_rules"].__setitem__(0, {"attribute": "items.variant.id", "operator": "in", "values": [{"value": "variant_1"}]}), "condiciones que la central no maneja"),
        (lambda r: r["application_method"]["target_rules"][0].update(operator="nin"), "condiciones que la central no maneja"),
    ],
)
def test_other_medusa_shapes_are_read_only_with_reason(mutate, reason_fragment) -> None:
    raw = _medusa()
    mutate(raw)

    view = coupon_view_from_medusa(raw, now=_at(2026, 9, 23))

    assert view.manageable is False
    assert reason_fragment in view.unmanageable_reason


def test_promotion_whose_campaign_is_shared_is_not_manageable() -> None:
    """A2: en Medusa Admin se puede colgar varias promociones de UNA campaña;
    editar fechas o nombre de "este" cupón cambiaría los de los otros."""
    view = coupon_view_from_medusa(_medusa(), now=_at(2026, 9, 23), campaign_shared=True)

    assert view.manageable is False
    assert "comparten varios cupones" in view.unmanageable_reason
    assert "Medusa Admin" in view.unmanageable_reason


def test_whole_catalog_coupon_reads_as_all_products() -> None:
    raw = _medusa()
    raw["application_method"]["target_rules"] = []

    view = coupon_view_from_medusa(raw, now=_at(2026, 9, 23))

    assert view.products is None
    assert view.manageable is True


def test_campaign_ending_mid_day_keeps_that_day_as_last() -> None:
    # Creada a mano en Medusa: cierra el 27 a las 18:00 Bogotá → el 27 cuenta (en parte).
    raw = _medusa()
    raw["campaign"]["ends_at"] = "2026-09-27T23:00:00.000Z"

    view = coupon_view_from_medusa(raw, now=_at(2026, 9, 23))

    assert view.ends_on == date(2026, 9, 27)
