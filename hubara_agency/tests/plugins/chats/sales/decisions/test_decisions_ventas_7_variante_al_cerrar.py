"""`ventas-7`: el cliente quiere cerrar y falta la variante → el selector va en
ese turno (2026-10-14, ···0686).

El bot preguntó «¿rojo y verde o multicolor?» y la clienta contestó «Dos
cajas de 12, ¿cómo sería el envío y el pago?». El contrato del turno solo
pedía el selector si ella preguntaba por colores; respondió el envío y el pago
y el color no volvió. Ahora, en la etapa de variantes (que ya sabe de la
opción «Color» de Medusa: test_order_real_options.py), preguntar cómo pagar,
dar los datos de envío o confirmar pide `present_variant_picker` en ese turno;
el envío sigue pidiendo sus tarifas. En otra etapa no piden nada, y
`ventas-6` (publicado) no cambia.
"""
from __future__ import annotations

from typing import Any

import pytest

from src.plugins.chats.shared.store_pack import BUNDLES_DIR, CATALOG_PATH
from tests.plugins.chats.sales.decisions.test_decisions_ventas_7 import IN_FORGE_CLONE

CLOSING = ("pagos", "datos_envio", "confirma_compra")


def _turn(name: str) -> Any:
    from src.sdk.decisionkit import load_bundle

    if IN_FORGE_CLONE:
        pytest.skip("clon de forge: los paquetes de prueba de esta tienda no viajan")
    return load_bundle(BUNDLES_DIR / name, CATALOG_PATH).turn


def _asks(turn: Any, topics: list[str], stage: str | None) -> list[tuple[str, list[str]]]:
    return [(r["topic"], r["any_of"]) for r in turn.required(topics, p={}, inp={"stage": stage})]


@pytest.mark.parametrize("topic", CLOSING)
def test_wanting_to_close_with_the_variant_missing_asks_for_the_picker(topic: str) -> None:
    assert _asks(_turn("ventas-7"), [topic], "etapa_variantes") == [(topic, ["present_variant_picker"])]


def test_the_incident_shipping_gets_its_rates_and_payment_the_picker() -> None:
    assert _asks(_turn("ventas-7"), ["envio", "pagos"], "etapa_variantes") == [
        ("envio", ["send_shipping_rates"]),
        ("pagos", ["present_variant_picker"]),
    ]


@pytest.mark.parametrize("stage", ["etapa_descubrimiento", "etapa_datos_envio", "etapa_cierre", None])
def test_outside_the_variant_stage_closing_asks_for_no_tool(stage: str | None) -> None:
    assert _asks(_turn("ventas-7"), list(CLOSING), stage) == []


def test_ventas_6_is_unchanged() -> None:
    assert _asks(_turn("ventas-6"), list(CLOSING), "etapa_variantes") == []
