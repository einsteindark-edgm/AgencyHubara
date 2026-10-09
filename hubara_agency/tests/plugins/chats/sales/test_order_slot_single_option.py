"""Las variantes que el catálogo no deja elegir (incidente del 2026-10-09).

El cliente eligió un producto con UN solo aroma y UN solo color. El bot los
nombró en el texto («Es aroma a frutos rojos y viene en blanco») pero nunca
llamó `set_order_slot(aroma=…, color=…)`: la etapa del pedido exige aroma,
color y cantidad, así que se quedó en `etapa_variantes` todo el episodio
(con producto, cantidad, ciudad y método de pago ya anotados) y el guion le
siguió pidiendo variantes al bot en vez de llevarlo al formulario.

Es un hecho del catálogo, no una decisión: si el producto tiene UNA opción de
aroma o de color, `set_order_slot` la anota sola (la paleta real de
`metadata.colores` manda sobre los tags, igual que al validar). No pisa lo
que el modelo dio ni lo que borró, y no escribe nada si la llamada no dejó
ningún dato del ítem. Si el producto no tiene NINGUNA opción de un atributo,
el borrador lo marca y la etapa no lo espera.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog.dtos import CatalogManifestDTO, CatalogProductDTO, SearchResult
from src.plugins.chats.agent.sales.tools.order_draft import SetOrderSlotTool
from src.plugins.chats.agent.sales.use_cases.funnel_stage import (
    STAGE_DATOS_ENVIO,
    STAGE_VARIANTES,
    resolve_funnel_stage,
)
from src.plugins.chats.shared.draft_items import draft_items

_TRILOGIA = CatalogProductDTO(
    id="prod_trilogia", handle="trilogia-del-terror", title="Trilogía del Terror", status="published",
    tags=["Aroma: Frutos rojos", "Color: Blanco"], options={"Unico": ["Unico"]},
)
_VELON = CatalogProductDTO(
    id="prod_velon", handle="velon-amor-eterno", title="Velón Amor Eterno", status="published",
    tags=["Aroma: Lavanda", "Color: verde", "Color: Blanco"],
)
_SIN_COLOR = CatalogProductDTO(
    id="prod_sin_color", handle="vela-de-soya", title="Vela de Soya", status="published",
    tags=["Aroma: Vainilla", "Aroma: Coco"],
)
#: Tags de color viejos (uno solo), paleta real de dos colores: no hay opción única.
_PALETA = CatalogProductDTO(
    id="prod_duo", handle="duo-zodiacal", title="Duo Zodiacal", status="published",
    tags=["Aroma: Frutos rojos", "Color: gris"], options={"Signo": ["Aries", "Leo"]},
    metadata={"colores": "Aries: Rojo; Leo: Dorado"},
)


class _FakeCatalog:
    def __init__(self, down: bool = False) -> None:
        self.down = down

    async def search(self, q: str, *, limit: int = 10) -> SearchResult:
        if self.down:
            raise RuntimeError("catálogo caído")
        products = [_TRILOGIA, _VELON, _SIN_COLOR, _PALETA]
        return SearchResult(
            query=q, count=len(products), truncated=False, stale=False,
            manifest=CatalogManifestDTO(version="v1", fetched_at="2026-10-09T00:00:00Z", product_count=4),
            results=products,
        )


def _ctx() -> ToolContext:
    return ToolContext(session_key="wa_single", channel="whatsapp", chat_id="c")


def _tool(tmp_path: Path, *, down: bool = False) -> SetOrderSlotTool:
    return SetOrderSlotTool(workspace=tmp_path, vault_dir=tmp_path / "vault", catalog=_FakeCatalog(down))


async def _set(tool: SetOrderSlotTool, **kwargs) -> dict:
    return json.loads(await tool.execute_with_context(_ctx(), **kwargs))


def _meta(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "vault" / "wa_single" / "metadata.json").read_text(encoding="utf-8"))


def _item(tmp_path: Path) -> dict:
    (item,) = draft_items(_meta(tmp_path)["episodes"][-1]["order_draft"])
    return item


@pytest.mark.asyncio
async def test_the_only_aroma_and_color_are_noted_with_the_product(tmp_path: Path) -> None:
    result = await _set(_tool(tmp_path), producto="Trilogía del Terror", cantidad="1")

    item = _item(tmp_path)
    assert (item["aroma"], item["color"]) == ("Frutos rojos", "Blanco")
    assert result["auto_filled"] == {"aroma": "Frutos rojos", "color": "Blanco"}
    assert "aroma" not in result["captured"] and "color" not in result["captured"]


@pytest.mark.asyncio
async def test_with_the_only_options_noted_the_stage_moves_to_shipping(tmp_path: Path) -> None:
    tool = _tool(tmp_path)
    await _set(tool, producto="Trilogía del Terror", cantidad="1")

    assert resolve_funnel_stage(_meta(tmp_path)) == STAGE_DATOS_ENVIO


@pytest.mark.asyncio
async def test_an_attribute_with_several_options_is_still_the_customers_choice(tmp_path: Path) -> None:
    result = await _set(_tool(tmp_path), producto="Velón Amor Eterno", cantidad="1")

    item = _item(tmp_path)
    assert item["aroma"] == "Lavanda" and not item.get("color")
    assert result["auto_filled"] == {"aroma": "Lavanda"}
    assert resolve_funnel_stage(_meta(tmp_path)) == STAGE_VARIANTES


@pytest.mark.asyncio
async def test_what_the_model_said_or_cleared_is_never_overwritten(tmp_path: Path) -> None:
    tool = _tool(tmp_path)
    await _set(tool, producto="Trilogía del Terror", aroma="Frutos rojos", cantidad="1")
    assert _item(tmp_path)["aroma"] == "Frutos rojos"

    cleared = await _set(tool, producto="Trilogía del Terror", color="")

    assert not _item(tmp_path).get("color")
    assert "auto_filled" not in cleared or "color" not in cleared["auto_filled"]


@pytest.mark.asyncio
async def test_a_product_without_colors_does_not_wait_for_one(tmp_path: Path) -> None:
    await _set(_tool(tmp_path), producto="Vela de Soya", aroma="Coco", cantidad="2")

    assert not _item(tmp_path).get("color")
    assert resolve_funnel_stage(_meta(tmp_path)) == STAGE_DATOS_ENVIO


@pytest.mark.asyncio
async def test_the_real_palette_wins_over_a_stale_single_color_tag(tmp_path: Path) -> None:
    result = await _set(_tool(tmp_path), producto="Duo Zodiacal", cantidad="1")

    assert not _item(tmp_path).get("color")
    assert result.get("auto_filled") == {"aroma": "Frutos rojos"}


@pytest.mark.asyncio
async def test_without_the_catalog_nothing_is_guessed(tmp_path: Path) -> None:
    result = await _set(_tool(tmp_path, down=True), producto="Trilogía del Terror", cantidad="1")

    assert not _item(tmp_path).get("aroma") and not _item(tmp_path).get("color")
    assert "auto_filled" not in result
