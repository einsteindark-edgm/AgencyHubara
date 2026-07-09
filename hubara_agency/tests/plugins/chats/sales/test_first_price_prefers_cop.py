"""`_first_price` prefiere COP cuando el producto tiene múltiples monedas.

Caso real (sesión wa_573125671604): el Duo Zodiacal tiene precios
[usd 35000, cop 35000] en Medusa; la caption al cliente salió
"Duo Zodiacal · $35.000 usd" porque `_first_price` agarraba prices[0]
ciego. La tienda vende en COP — si hay precio COP, ese gana.
"""
from __future__ import annotations

from src.platform.catalog.dtos import (
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
)
from src.plugins.chats.agent.sales.tools.catalog import (
    _first_price as catalog_first_price,
)
from src.plugins.chats.agent.sales.tools.ui_intents import (
    _first_price as ui_first_price,
)


def _product(prices: list[CatalogPriceDTO]) -> CatalogProductDTO:
    return CatalogProductDTO(
        id="p1",
        handle="duo-zodiacal",
        title="Duo Zodiacal",
        status="published",
        variants=[CatalogVariantDTO(id="v1", title="Unico", prices=prices)],
    )


_USD_FIRST = [
    CatalogPriceDTO(amount="35000", currency_code="usd"),
    CatalogPriceDTO(amount="35000", currency_code="cop"),
]
_ONLY_USD = [CatalogPriceDTO(amount="12", currency_code="usd")]


def test_catalog_first_price_prefers_cop_over_usd():
    assert catalog_first_price(_product(_USD_FIRST)) == ("35000", "cop")


def test_ui_intents_first_price_prefers_cop_over_usd():
    assert ui_first_price(_product(_USD_FIRST)) == ("35000", "cop")


def test_first_price_falls_back_to_first_when_no_cop():
    assert catalog_first_price(_product(_ONLY_USD)) == ("12", "usd")
    assert ui_first_price(_product(_ONLY_USD)) == ("12", "usd")
