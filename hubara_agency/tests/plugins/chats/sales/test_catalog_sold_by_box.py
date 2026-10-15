"""El catálogo que lee el bot dice qué se cobra por caja y cuántas trae
(laboratorio de ···0686, 2026-10-14).

Las velas de deseos se cobran por caja: 12 la cuadrada, 10 la ondulada
(`ventas-7/domain.yaml: sold_by_box`). El resumen de `search_products` no lo
decía (la descripción larga solo va en el detalle) y salió caro: «quiero una
caja de las de 12 y una de las de 10» → «no manejamos cajas de 12 ni de 10»;
«Dos cajas de 12» → anotó las onduladas, que traen 10; «me vendes 36
velitas?» → se pasó a un colega como pedido al por mayor (eran 3 cajas).
Ahora el resumen y el detalle de los productos que la tienda declara llevan
la caja; los demás, y `ventas-6`, quedan igual.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog.dtos import (
    CatalogManifestDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
    SearchResult,
)
from src.plugins.chats.agent.sales.tools.catalog import GetProductByHandleTool, SearchProductsTool


def _product(handle: str, title: str, price: str) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=f"prod_{handle}", handle=handle, title=title, status="published", tags=["Aroma: Navidad"],
        variants=[CatalogVariantDTO(id=f"v_{handle}", title="Multicolor",
                                    prices=[CatalogPriceDTO(amount=price, currency_code="cop")])],
    )


CUADRADAS = _product("velas-de-deseos-cuadradas", "Velas de deseos cuadradas", "25000")
ONDULADAS = _product("velas-de-deseos-onduladas", "Velas de deseos onduladas", "22500")
LUZ = _product("luz-serena", "Luz Serena", "35000")


class _Catalog:
    async def search(self, q: str, limit: int = 10) -> SearchResult:
        products = [ONDULADAS, CUADRADAS, LUZ]
        return SearchResult(
            query=q, count=len(products), truncated=False, stale=False,
            manifest=CatalogManifestDTO(version="v1", fetched_at="2026-10-14T00:00:00Z", product_count=3),
            results=products,
        )

    async def get_by_handle(self, handle: str) -> CatalogProductDTO:
        return {p.handle: p for p in (ONDULADAS, CUADRADAS, LUZ)}[handle]


def _ctx() -> ToolContext:
    return ToolContext(session_key="s", channel="whatsapp", chat_id="c")


async def _search(tmp_path: Path) -> dict[str, dict]:
    tool = SearchProductsTool(workspace=tmp_path, catalog=_Catalog())
    payload = json.loads(await tool.execute_with_context(_ctx(), q="velitas"))
    return {p["handle"]: p for p in payload["results"]}


async def _detail(handle: str, tmp_path: Path) -> dict:
    tool = GetProductByHandleTool(workspace=tmp_path, catalog=_Catalog())
    return json.loads(await tool.execute_with_context(_ctx(), handle=handle))["product"]


@pytest.fixture
def ventas_7(monkeypatch):
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-7")


@pytest.mark.asyncio
async def test_the_search_says_how_many_each_box_brings(tmp_path: Path, ventas_7) -> None:
    found = await _search(tmp_path)

    assert found["velas-de-deseos-cuadradas"]["por_caja"] == 12
    assert found["velas-de-deseos-onduladas"]["por_caja"] == 10
    sold = found["velas-de-deseos-cuadradas"]["se_vende_por_caja"]
    assert "caja de 12" in sold and "precio es el de la caja" in sold
    # La cuenta que salió mal, con su producto, y la cantidad en cifras
    # (en el laboratorio se anotó «3 cajas (36 unidades)»).
    assert "la cantidad del pedido es el número de cajas (36 unidades → cantidad 3)" in sold


@pytest.mark.asyncio
async def test_the_detail_says_it_too(tmp_path: Path, ventas_7) -> None:
    detail = await _detail("velas-de-deseos-onduladas", tmp_path)

    assert detail["por_caja"] == 10 and "caja de 10" in detail["se_vende_por_caja"]


@pytest.mark.asyncio
async def test_a_product_sold_by_the_unit_says_nothing(tmp_path: Path, ventas_7) -> None:
    found = await _search(tmp_path)

    assert "por_caja" not in found["luz-serena"] and "se_vende_por_caja" not in found["luz-serena"]
    assert "por_caja" not in await _detail("luz-serena", tmp_path)


@pytest.mark.asyncio
async def test_with_ventas_6_nothing_changes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SALES_DECISIONS_BUNDLE", "ventas-6")

    found = await _search(tmp_path)

    assert "por_caja" not in found["velas-de-deseos-cuadradas"]
