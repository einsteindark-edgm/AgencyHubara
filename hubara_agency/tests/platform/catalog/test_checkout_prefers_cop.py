"""Premortem PR variantes: el verify de checkout debe preferir COP.

`_first_price`/`_first_live_price` de `medusa_checkout` tomaban
`variants[0].prices[0]` a ciegas — con multi-currency (usd listado primero,
caso real del Duo Zodiacal v1) el verify comparaba y REPORTABA el precio en
usd, y el LLM cita esa currency al cliente ("$35.000 usd"). Misma regla
COP-first que tools/catalog.py y el mapper de Meta.

También: si `variants[0]` no tiene precios pero otra variante sí (producto
multi-variante a medio cargar), el verify usa la primera variante CON precio
en vez de reportar precio None (falsa discrepancia → escalación).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.platform.catalog.checkout_port import CheckoutItem
from src.platform.catalog.dtos import (
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
)
from src.platform.catalog.medusa_checkout import MedusaCheckoutVerification


class _FakePrice:
    def __init__(self, amount: str, currency: str) -> None:
        self.amount = Decimal(amount)
        self.currency_code = currency


class _FakeVariant:
    def __init__(self, prices: list[_FakePrice]) -> None:
        self.prices = prices


class _FakeLiveProduct:
    def __init__(self, title: str, variants: list[_FakeVariant]) -> None:
        self.title = title
        self.variants = variants


class _FakePage:
    def __init__(self, products: list[_FakeLiveProduct]) -> None:
        self.products = products


class _FakeMedusa:
    def __init__(self, page: _FakePage) -> None:
        self._page = page

    async def list(self, handle: str, limit: int = 1) -> _FakePage:
        return self._page


class _FakeSnapshot:
    def __init__(self, product: CatalogProductDTO) -> None:
        self._product = product

    async def get_by_handle(self, handle: str) -> CatalogProductDTO:
        return self._product


def _snap_duo(variants: list[CatalogVariantDTO]) -> CatalogProductDTO:
    return CatalogProductDTO(
        id="p1",
        handle="duo-zodiacal",
        title="Duo Zodiacal",
        status="published",
        variants=variants,
    )


@pytest.mark.asyncio
async def test_verify_reports_cop_not_usd():
    snap = _snap_duo(
        [
            CatalogVariantDTO(
                id="v1",
                title="Leo",
                prices=[
                    CatalogPriceDTO(amount="35000", currency_code="usd"),
                    CatalogPriceDTO(amount="35000", currency_code="cop"),
                ],
            )
        ]
    )
    live = _FakePage(
        [
            _FakeLiveProduct(
                "Duo Zodiacal",
                [
                    _FakeVariant(
                        [
                            _FakePrice("35000", "usd"),
                            _FakePrice("35000", "cop"),
                        ]
                    )
                ],
            )
        ]
    )
    verifier = MedusaCheckoutVerification(
        medusa=_FakeMedusa(live), snapshot=_FakeSnapshot(snap)
    )
    result = await verifier.verify_items(
        [CheckoutItem(handle="duo-zodiacal", quantity=1)]
    )
    assert result.catalog_available is True
    item = result.items[0]
    assert item.currency == "cop"
    assert item.discrepancy is False


@pytest.mark.asyncio
async def test_verify_uses_first_priced_variant():
    snap = _snap_duo(
        [
            CatalogVariantDTO(id="v0", title="Leo", prices=[]),
            CatalogVariantDTO(
                id="v1",
                title="Escorpion",
                prices=[CatalogPriceDTO(amount="35000", currency_code="cop")],
            ),
        ]
    )
    live = _FakePage(
        [
            _FakeLiveProduct(
                "Duo Zodiacal",
                [
                    _FakeVariant([]),
                    _FakeVariant([_FakePrice("35000", "cop")]),
                ],
            )
        ]
    )
    verifier = MedusaCheckoutVerification(
        medusa=_FakeMedusa(live), snapshot=_FakeSnapshot(snap)
    )
    result = await verifier.verify_items(
        [CheckoutItem(handle="duo-zodiacal", quantity=1)]
    )
    item = result.items[0]
    assert item.snapshot_price == "35000"
    assert item.live_price == "35000"
    assert item.discrepancy is False
