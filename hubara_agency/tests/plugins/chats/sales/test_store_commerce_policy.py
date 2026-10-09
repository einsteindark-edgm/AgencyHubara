"""La política comercial es de la TIENDA, no del motor (forge, 2026-10-09).

Tarifas de envío, zona local, mínimo de contra entrega, recargos del link de
pago, prefijo de SKU, dominio de la tienda y colecciones del catálogo nacen
en Terraform (`tenants.<t>.store` → SSM → `.env`) y el código los lee del
entorno. Sin variable manda el valor de hoy (Hubara): su bot no cambia ni un
carácter. Un clon de forge pone los suyos y el bot le habla con ellos.
"""
from __future__ import annotations

import pytest

from src.plugins.catalog.agent.composition import allowed_collection_handles
from src.plugins.chats.agent.sales.config import payments, shipping, store_codes
from src.plugins.chats.agent.sales.use_cases.photo_product import sku_pattern
from src.plugins.chats.agent.sales.use_cases.web_product_ref import product_ref_pattern

OTRA_TIENDA = {
    "SHIPPING_LOCAL_ZONE": "Medellín y el área metropolitana",
    "SHIPPING_LOCAL_CITY": "Medellín",
    "SHIPPING_RATE_LOCAL_COP": "9000",
    "SHIPPING_RATE_NATIONAL_COP": "18500",
    "CASH_ON_DELIVERY_MIN_COP": "60000",
}


# ── Hubara (sin variables): idéntico a hoy ───────────────────────────────────


def test_without_config_the_store_keeps_todays_policy() -> None:
    policy = shipping.ShippingPolicy.from_env({})

    assert (policy.local_cop, policy.national_cop, policy.cod_min_products_cop) == (7_900, 16_940, 45_000)
    assert shipping.rates_message(policy) == (
        "Nuestras tarifas mínimas de envío son 🚚:\n"
        "• Bogotá y municipios cercanos: $7.900\n"
        "• Nivel Nacional: $16.940\n"
        "El valor definitivo se confirma al despachar según el tamaño y "
        "peso de tu paquete📏📦📦"
    )
    assert payments.PaymentPolicy.from_env({}).link_surcharges == ("1,5%", "2,69%")
    # el dominio por defecto es el de la tienda del repo (en un clon, forge pone el suyo)
    assert store_codes.StoreCodes.from_env({}) == store_codes.StoreCodes()
    assert store_codes.StoreCodes().sku_prefix == "HUB-"
    assert allowed_collection_handles({}) is None  # el default del caso de uso


def test_the_suite_runs_with_the_engine_policy_whatever_the_shell_exports() -> None:
    """tests/conftest.py limpia STORE_POLICY_ENV antes de importar src."""
    assert shipping.POLICY == shipping.ShippingPolicy()
    assert payments.POLICY == payments.PaymentPolicy()
    assert store_codes.CODES == store_codes.StoreCodes()


# ── Otra tienda: el bot habla con SU política ────────────────────────────────


def test_another_store_publishes_its_own_rates_and_zone() -> None:
    policy = shipping.ShippingPolicy.from_env(OTRA_TIENDA)

    message = shipping.rates_message(policy)
    assert "• Medellín y el área metropolitana: $9.000\n" in message
    assert "• Nivel Nacional: $18.500\n" in message
    assert "Bogotá" not in shipping.rate_rule(policy) and "$9.000" in shipping.rate_rule(policy)
    assert "Bogotá" not in shipping.shipping_cop_param_description(policy)


def test_the_local_zone_is_the_store_city() -> None:
    policy = shipping.ShippingPolicy.from_env(OTRA_TIENDA)

    assert shipping.shipping_zone("medellin", policy) == shipping.SHIPPING_ZONE_BOGOTA
    assert shipping.shipping_zone("Bogotá D.C.", policy) is None
    assert shipping.shipping_rate_for_city("Medellín", policy) == 9_000
    assert shipping.shipping_rate_for_city("Cali", policy) == 18_500


def test_cash_on_delivery_uses_the_store_minimum_inclusive() -> None:
    policy = shipping.ShippingPolicy.from_env(OTRA_TIENDA)

    assert shipping.cash_on_delivery_available(59_999, policy) is False
    assert shipping.cash_on_delivery_available(60_000, policy) is True


def test_payment_link_surcharges_come_from_the_store() -> None:
    policy = payments.PaymentPolicy.from_env(
        {"PAYMENT_LINK_SURCHARGE_LOCAL": "2%", "PAYMENT_LINK_SURCHARGE_OTHER": "3,1%"}
    )

    assert policy.link_surcharges == ("2%", "3,1%")


def test_the_store_sku_prefix_drives_refs_and_photos() -> None:
    codes = store_codes.StoreCodes.from_env({"STORE_SKU_PREFIX": "acm-"})

    assert codes.sku_prefix == "ACM-"
    ref = product_ref_pattern(codes.sku_prefix)
    assert ref.search("📦 ref: ACM-CAFE-01 · via: chatgpt").group(1) == "ACM-CAFE-01"
    assert ref.search("ref: HUB-CUBO") is None
    assert sku_pattern(codes.sku_prefix).findall("código ACM-CAFE en la foto") == ["ACM-CAFE"]


def test_the_collections_that_enter_the_catalog_are_the_store_ones() -> None:
    assert allowed_collection_handles({"CATALOG_COLLECTION_HANDLES": "vitrina, Nuevos ,"}) == frozenset(
        {"vitrina", "nuevos"}
    )
    # vacío a propósito = solo productos sin colección
    assert allowed_collection_handles({"CATALOG_COLLECTION_HANDLES": ""}) == frozenset()


# ── Una política mal escrita no arranca (fail-closed, nunca un precio raro) ──


@pytest.mark.parametrize(
    ("var", "value"),
    [("SHIPPING_RATE_LOCAL_COP", "7.900"), ("SHIPPING_RATE_NATIONAL_COP", "0"), ("CASH_ON_DELIVERY_MIN_COP", "-1")],
)
def test_a_bad_amount_fails_naming_the_variable(var: str, value: str) -> None:
    with pytest.raises(ValueError, match=var):
        shipping.ShippingPolicy.from_env({var: value})
