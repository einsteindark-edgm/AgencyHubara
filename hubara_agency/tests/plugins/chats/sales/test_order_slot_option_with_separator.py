"""set_order_slot acepta opciones del catálogo que traen « y » en el nombre.

Incidente pedido #78 (2026-10-13): el cliente eligió en el carrito «Velas de
deseos onduladas · Rojo y verde». `set_order_slot(diseno="Rojo y verde")` se
rechazó dos veces con `invalid: [Rojo, verde]` y `available: [Rojo y verde,
Multicolor]`: el separador de elecciones múltiples (« y ») partía la opción
antes de compararla con la lista. El borrador quedó sin variante, el registro
mandó el aroma como `variant_label` y Medusa cayó a la primera variante (en
las cuadradas, «Multicolor»: la equivocada).
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
from src.plugins.chats.agent.sales.tools.order_draft import SetOrderSlotTool

_DESEOS = CatalogProductDTO(
    id="prod_deseos",
    handle="velas-de-deseos-cuadradas",
    title="Velas de deseos cuadradas",
    status="published",
    tags=["Aroma: Navidad"],
    options={"Color": ["Multicolor", "Rojo y verde"]},
    variants=[
        CatalogVariantDTO(
            id=f"v_{i}",
            title=value,
            options={"Color": value},
            prices=[CatalogPriceDTO(amount="22500", currency_code="cop")],
        )
        for i, value in enumerate(("Multicolor", "Rojo y verde"))
    ],
)


_TAG_COLORS = CatalogProductDTO(
    id="prod_tags",
    handle="vela-navidena",
    title="Vela navideña",
    status="published",
    tags=["Color: Rojo y verde", "Color: Azul"],
    variants=[
        CatalogVariantDTO(
            id="v_unico",
            title="Unico",
            prices=[CatalogPriceDTO(amount="20000", currency_code="cop")],
        )
    ],
)

_DUO = CatalogProductDTO(
    id="prod_duo",
    handle="duo-zodiacal",
    title="Duo Zodiacal",
    status="published",
    options={"Signo": ["Leo", "Libra"]},
    variants=[
        CatalogVariantDTO(
            id=f"v_{s.lower()}",
            title=s,
            options={"Signo": s},
            prices=[CatalogPriceDTO(amount="35000", currency_code="cop")],
        )
        for s in ("Leo", "Libra")
    ],
)


class _FakeCatalog:
    def __init__(self, *products: CatalogProductDTO) -> None:
        self._products = list(products)

    async def search(self, q: str, limit: int = 10):
        return SearchResult(
            query=q,
            count=len(self._products),
            truncated=False,
            stale=False,
            manifest=CatalogManifestDTO(
                version="v1",
                fetched_at="2026-10-13T00:00:00Z",
                product_count=len(self._products),
            ),
            results=self._products,
        )

    async def get_by_handle(self, handle: str):
        return next((p for p in self._products if p.handle == handle), None)


def _tool(tmp_path: Path, *products: CatalogProductDTO) -> SetOrderSlotTool:
    return SetOrderSlotTool(
        workspace=tmp_path,
        vault_dir=tmp_path / "vault",
        catalog=_FakeCatalog(*products),
    )


async def _call(tool: SetOrderSlotTool, **kwargs) -> dict:
    ctx = ToolContext(session_key="wa_78", channel="whatsapp", chat_id="c")
    return json.loads(await tool.execute_with_context(ctx, **kwargs))


async def _set(tmp_path: Path, product: CatalogProductDTO, **kwargs) -> dict:
    return await _call(_tool(tmp_path, product), **kwargs)


@pytest.mark.asyncio
async def test_diseno_option_with_y_in_its_name_is_captured(tmp_path):
    result = await _set(
        tmp_path, _DESEOS,
        producto="Velas de deseos cuadradas", cantidad="1", diseno="Rojo y verde",
    )
    assert result.get("rejected", []) == []
    assert result["order_draft"]["diseno"] == "Rojo y verde"


@pytest.mark.asyncio
async def test_diseno_two_choices_keep_the_option_with_y_whole(tmp_path):
    result = await _set(
        tmp_path, _DESEOS,
        producto="Velas de deseos cuadradas", diseno="Multicolor y Rojo y verde",
    )
    assert result.get("rejected", []) == []
    assert result["order_draft"]["diseno"] == "Multicolor, Rojo y verde"


@pytest.mark.asyncio
async def test_color_tag_with_y_in_its_name_is_captured(tmp_path):
    result = await _set(
        tmp_path, _TAG_COLORS, producto="Vela navideña", color="rojo y verde",
    )
    assert result.get("rejected", []) == []
    assert result["order_draft"]["color"] == "Rojo y verde"


@pytest.mark.asyncio
async def test_option_with_y_of_another_product_says_which_product_it_is(tmp_path):
    tool = _tool(tmp_path, _DUO, _DESEOS)
    await _call(tool, producto="Duo Zodiacal")

    result = await _call(tool, diseno="Rojo y verde")

    (rejection,) = result["rejected"]
    assert rejection.get("belongs_to") == ["Velas de deseos cuadradas"]
