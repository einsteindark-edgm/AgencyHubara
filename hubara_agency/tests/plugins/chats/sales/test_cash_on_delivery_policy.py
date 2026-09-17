"""Política de contra entrega — una sola fuente (`config/shipping.py`).

Incidente run ebbc203d (2026-09-16): el guion decía "contra entrega desde
$45.000 en productos", el bot se lo afirmó a la clienta, y el código usaba
`> 45000` estricto → el formulario ocultó la opción para un pedido de $45.000
exactos. El umbral es INCLUSIVO y vive en un único lugar.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales.config.shipping import (
    CASH_ON_DELIVERY_MIN_PRODUCTS_COP,
    cash_on_delivery_available,
)


def test_cod_threshold_is_45000_in_products() -> None:
    assert CASH_ON_DELIVERY_MIN_PRODUCTS_COP == 45_000


def test_cod_available_at_exact_threshold() -> None:
    assert cash_on_delivery_available(45_000) is True


def test_cod_available_above_threshold() -> None:
    assert cash_on_delivery_available(49_500) is True


def test_cod_not_available_below_threshold() -> None:
    assert cash_on_delivery_available(44_999) is False
    assert cash_on_delivery_available(0) is False
