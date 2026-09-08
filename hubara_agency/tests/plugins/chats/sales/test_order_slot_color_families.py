"""set_order_slot tolera la GAMA de un color (requisito del operador 2026-09-08).

Antes: "azul clarito" contra el tag "Color: Azul" → `rejected` → el guion
decía "el rojo no lo manejo" → respuesta cortante. Ahora el tono se resuelve
a la familia del catálogo (config por tenant), se captura el color REAL, el
tono pedido queda en `notas` para el operador, y el envelope le pide al LLM
que confirme el tono sin negarlo ni prometerlo.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog.color_families_loader import (
    DEFAULT_COLOR_FAMILIES_PATH,
    ColorFamiliesLoader,
)
from src.platform.catalog.dtos import (
    CatalogManifestDTO,
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
    SearchResult,
)
from src.plugins.chats.agent.sales.tools.order_draft import SetOrderSlotTool

FAM = ColorFamiliesLoader(DEFAULT_COLOR_FAMILIES_PATH).load()


def _product(handle: str, title: str, tags: list[str], **kw) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=f"prod_{handle}",
        handle=handle,
        title=title,
        status="published",
        tags=tags,
        variants=[
            CatalogVariantDTO(
                id=f"v_{handle}",
                title="Unico",
                options={},
                prices=[CatalogPriceDTO(amount="35000", currency_code="cop")],
            )
        ],
        **kw,
    )


_LUZ = _product(
    "luz-serena", "Luz Serena",
    ["Aroma: Lavanda", "Color: Azul", "Color: Blanco", "Color: Lila", "Color: Morado"],
)
_DUO = _product(
    "duo-zodiacal", "Duo Zodiacal", ["Color: gris"],
    metadata={"colores": "Aries: rojo; Leo: naranja; Acuario: azul petróleo"},
    options={"Signo": ["Aries", "Leo", "Acuario"]},
)


class _FakeCatalog:
    async def search(self, q: str, limit: int = 10):
        return SearchResult(
            query=q, count=2, truncated=False, stale=False,
            manifest=CatalogManifestDTO(
                version="v1", fetched_at="2026-09-08T00:00:00Z", product_count=2,
            ),
            results=[_LUZ, _DUO],
        )

    async def get_by_handle(self, handle: str):
        return _LUZ if handle == _LUZ.handle else _DUO


def _tool(tmp_path: Path, families=FAM) -> SetOrderSlotTool:
    return SetOrderSlotTool(
        workspace=tmp_path,
        vault_dir=tmp_path / "vault",
        catalog=_FakeCatalog(),
        color_families=families,
    )


def _ctx() -> ToolContext:
    return ToolContext(session_key="wa_cf", channel="whatsapp", chat_id="c")


async def _set(tool, **kwargs) -> dict:
    return json.loads(await tool.execute_with_context(_ctx(), **kwargs))


@pytest.mark.asyncio
async def test_shade_captures_family_color_and_keeps_tone_in_notas(tmp_path):
    tool = _tool(tmp_path)
    out = await _set(tool, producto="Luz Serena", color="azul clarito")
    assert "rejected" not in out
    assert out["captured"]["color"] == "Azul"
    assert out["order_draft"]["color"] == "Azul"
    assert "azul clarito" in out["order_draft"]["notas"]
    fam = out["color_family"]
    assert fam["requested"] == "azul clarito"
    assert fam["captured"] == "Azul"
    assert fam["family"] == "Azul"
    assert fam["shade_requested"] is True
    # El envelope le dice al LLM cómo hablar: confirmar sin negar ni prometer.
    assert "azul clarito" in out["summary"]
    assert "no lo manejo" not in out["summary"].casefold()


@pytest.mark.asyncio
async def test_plural_or_gender_variation_captures_without_tone_ceremony(tmp_path):
    tool = _tool(tmp_path)
    out = await _set(tool, producto="Luz Serena", color="azules")
    assert out["captured"]["color"] == "Azul"
    assert out["color_family"]["shade_requested"] is False
    assert "azules" not in out["order_draft"].get("notas", "")


@pytest.mark.asyncio
async def test_exact_color_still_captures_without_family_envelope(tmp_path):
    tool = _tool(tmp_path)
    out = await _set(tool, producto="Luz Serena", color="lila")
    assert out["captured"]["color"] == "Lila"
    assert "color_family" not in out


@pytest.mark.asyncio
async def test_shade_with_two_catalog_colors_in_family_is_offered_not_guessed(tmp_path):
    # Lila y Morado son colores distintos del catálogo; "morado clarito" está
    # declarado en lila → un solo candidato. "violeta" → morado. Pero un tono
    # ambiguo entre familias ("lila o morado") NO se adivina.
    tool = _tool(tmp_path)
    out = await _set(tool, producto="Luz Serena", color="lila o morado")
    assert "color" not in out["captured"]
    rej = out["rejected"][0]
    assert rej["field"] == "color"
    assert rej["reason"] == "color_family_ambiguous"
    assert rej["candidates"] == ["Lila", "Morado"]
    assert "Lila" in out["summary"] and "Morado" in out["summary"]


@pytest.mark.asyncio
async def test_family_not_in_catalog_rejects_with_family_and_palette(tmp_path):
    tool = _tool(tmp_path)
    out = await _set(tool, producto="Luz Serena", color="vinotinto")
    rej = out["rejected"][0]
    assert rej["field"] == "color"
    assert rej["reason"] == "color_family_not_offered"
    assert rej["family"] == "Rojo"
    assert rej["available"] == ["Azul", "Blanco", "Lila", "Morado"]
    assert "Rojo" in out["summary"] or "rojo" in out["summary"]


@pytest.mark.asyncio
async def test_unknown_word_keeps_legacy_rejection(tmp_path):
    tool = _tool(tmp_path)
    out = await _set(tool, producto="Luz Serena", color="chartreuse")
    rej = out["rejected"][0]
    assert rej["field"] == "color"
    assert "reason" not in rej
    assert rej["available"] == ["Azul", "Blanco", "Lila", "Morado"]


@pytest.mark.asyncio
async def test_variant_colors_product_resolves_shade_to_alias_and_owner_sign(tmp_path):
    # Duo Zodiacal: los colores reales viven en metadata.colores; "celeste"
    # no es alias literal pero cae en la familia azul → "azul petróleo"
    # (Acuario). El envelope ya sabe qué signo lo tiene.
    tool = _tool(tmp_path)
    out = await _set(tool, producto="Duo Zodiacal", color="celeste")
    assert "rejected" not in out
    assert out["captured"]["color"] == "azul petróleo"
    assert out["color_family"]["requested"] == "celeste"
    assert [s["value"] for s in out["signs_for_color"]] == ["Acuario"]


@pytest.mark.asyncio
async def test_multi_color_request_resolves_each_token(tmp_path):
    tool = _tool(tmp_path)
    out = await _set(tool, producto="Luz Serena", color="celeste y hueso")
    assert out["captured"]["color"] == "Azul, Blanco"
    assert out["color_family"]["requested"] == "celeste y hueso"


@pytest.mark.asyncio
async def test_without_families_config_behaviour_is_legacy_exact_match(tmp_path):
    from src.platform.catalog.color_families import EMPTY_COLOR_FAMILIES

    tool = _tool(tmp_path, families=EMPTY_COLOR_FAMILIES)
    out = await _set(tool, producto="Luz Serena", color="azul clarito")
    assert out["rejected"][0]["field"] == "color"
    assert "color_family" not in out
