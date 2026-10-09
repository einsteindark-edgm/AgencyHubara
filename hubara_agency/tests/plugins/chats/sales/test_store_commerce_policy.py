"""La política comercial es de la TIENDA, no del motor (forge, 2026-10-09).

Tarifas de envío, zona local, mínimo de contra entrega, recargos del link de
pago, prefijo de SKU, dominio de la tienda y colecciones del catálogo nacen
en Terraform (`tenants.<t>.store` → SSM → `.env`) y el código los lee del
entorno. Sin variable manda el valor de hoy (Hubara): su bot no cambia ni un
carácter. Un clon de forge pone los suyos y el bot le habla con ellos.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

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
#: Una tienda completa: lo que render-env-from-ssm.sh baja al .env de un clon.
OTRA_TIENDA_COMPLETA = {
    **OTRA_TIENDA,
    "PAYMENT_NEQUI_NUMBER": "3001112233",
    "PAYMENT_LINK_SURCHARGE_LOCAL": "2%",
    "PAYMENT_LINK_SURCHARGE_OTHER": "3,1%",
    "STORE_SKU_PREFIX": "AUR-",
    "STORE_WEB_DOMAIN": "cafeaurora.co",
    "CATALOG_COLLECTION_HANDLES": "vitrina,nuevos",
}
_BACKEND = Path(__file__).resolve().parents[4]


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


# Las descripciones de las tools del bot de ventas se arman AL IMPORTAR: se
# importan en otro proceso, con el .env de la otra tienda (premortem 2026-10-09:
# `present_order_confirmation` le decía al LLM «Bogotá y cercanos $9.000»).
_TOOL_TEXTS = """
import importlib, json, pkgutil
import src.plugins.chats.agent.sales.tools as pkg
out = []
for info in pkgutil.iter_modules(pkg.__path__):
    mod = importlib.import_module(f"{pkg.__name__}.{info.name}")
    for name, obj in vars(mod).items():
        if isinstance(obj, type) and obj.__module__ == mod.__name__:
            for attr in ("description", "parameters"):
                value = getattr(obj, attr, None)
                if isinstance(value, (str, dict)):
                    out.append(f"{info.name}.{name}.{attr}: {json.dumps(value, ensure_ascii=False)}")
print(json.dumps(out, ensure_ascii=False))
"""
_MOTHER_POLICY = re.compile(r"Bogot|7\.900|16\.940|45\.000|1,5 ?%|2,69 ?%")


def test_another_store_tools_never_quote_the_mother_policy() -> None:
    env = {**os.environ, **OTRA_TIENDA_COMPLETA, "MEDUSA_BASE_URL": "http://dummy", "MEDUSA_ADMIN_TOKEN": "dummy"}
    proc = subprocess.run(
        [sys.executable, "-c", _TOOL_TEXTS], cwd=_BACKEND, env=env, capture_output=True, text=True, timeout=180
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    texts = json.loads(proc.stdout.strip().splitlines()[-1])

    assert len(texts) > 20  # de verdad recorrió las tools
    assert [t for t in texts if _MOTHER_POLICY.search(t)] == []


# ── Una política mal escrita no arranca (fail-closed, nunca un precio raro) ──


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("SHIPPING_RATE_LOCAL_COP", "7.900"),
        ("SHIPPING_RATE_NATIONAL_COP", "0"),
        ("CASH_ON_DELIVERY_MIN_COP", "-1"),
        # `9.000` en HCL es el número 9: Terraform lo baja como "9" (premortem 2026-10-09)
        ("SHIPPING_RATE_LOCAL_COP", "9"),
        ("CASH_ON_DELIVERY_MIN_COP", "999"),
    ],
)
def test_a_bad_amount_fails_naming_the_variable(var: str, value: str) -> None:
    with pytest.raises(ValueError, match=var):
        shipping.ShippingPolicy.from_env({var: value})


@pytest.mark.parametrize("city", ["Bogotá D.C.", "Medellín, Antioquia", "Cali 2"])
def test_the_local_city_is_a_plain_city_name(city: str) -> None:
    """La zona local se reconoce por la ciudad contenida en la del cliente: con
    «Bogotá D.C.», un cliente que escribe «Bogotá» caería en la tarifa nacional."""
    with pytest.raises(ValueError, match="SHIPPING_LOCAL_CITY"):
        shipping.ShippingPolicy.from_env({"SHIPPING_LOCAL_CITY": city})
