"""El mapper debe PREFERIR el precio en COP cuando Medusa devuelve varias
monedas por variante.

Regresión de prod (2026-06-30): Medusa devuelve `variants[0].prices` con dos
entradas — `usd` y `cop` (mismo amount) — y el mapper tomaba `prices[0]` a
ciegas, que para algunos productos era el USD → la card de WhatsApp mostraría
"35000 USD" (≈ $35.000 dólares) en vez de "35000 COP".
"""
from __future__ import annotations

from src.platform.catalog.dtos import (
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
)
from src.platform.meta_catalog.mapper import map_product_to_meta


def _prod(prices: list[CatalogPriceDTO]) -> CatalogProductDTO:
    return CatalogProductDTO(
        id="p1",
        handle="vela",
        title="Vela",
        status="published",
        description="desc",
        thumbnail="https://img.example/x.jpg",
        variants=[CatalogVariantDTO(id="v1", title="Unico", sku=None, prices=prices)],
        images=[],
        tags=[],
        categories=[],
        metadata=None,
    )


def _price(amount: str, currency: str) -> CatalogPriceDTO:
    return CatalogPriceDTO(
        amount=amount, currency_code=currency, min_quantity=None, max_quantity=None
    )


def test_mapper_prefers_cop_when_usd_listed_first():
    item = map_product_to_meta(_prod([_price("35000", "usd"), _price("35000", "cop")]))
    assert item is not None
    assert item.price == "35000 COP"  # NO "35000 USD"


def test_mapper_uses_cop_when_only_cop_present():
    item = map_product_to_meta(_prod([_price("23000", "cop")]))
    assert item is not None
    assert item.price == "23000 COP"
