"""set_order_slot con mapeo color↔signo (Duo Zodiacal).

Cada signo viene en UN color fijo (metadata `colores` del producto). El
pedido real del cliente suele ser "quiero la de Leo en rojo" — combinación
que NO existe (Leo es naranja; el rojo es de Aries). El contrato:

1. El color se valida contra los colores REALES de las variantes (los tags
   del producto están stale: "rojo" debe aceptarse aunque no haya tag).
2. Combinación color+signo inexistente → el valor NUEVO se rechaza con
   `reason: color_sign_mismatch` + las alternativas mismo-color-otro-signo,
   para que el bot diga "no es el signo, pero SÍ es el color" (nunca negar
   el color ni guardar una combinación que no existe).
3. Color solo (sin signo) → se guarda + hint `signs_for_color` para que el
   bot muestre el signo dueño del color de una.
4. Producto sin mapeo → comportamiento previo intacto (tags closed-list).
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

_DUO = CatalogProductDTO(
    id="prod_duo",
    handle="duo-zodiacal",
    title="Duo Zodiacal",
    status="published",
    tags=["Color: gris"],  # stale a propósito: los colores reales van abajo
    metadata={
        "colores": "Aries: rojo; Leo: naranja; Acuario: azul claro, celeste"
    },
    options={"Signo": ["Aries", "Leo", "Acuario"]},
    variants=[
        CatalogVariantDTO(
            id=f"v_{s.lower()}",
            title=s,
            options={"Signo": s},
            prices=[CatalogPriceDTO(amount="35000", currency_code="cop")],
        )
        for s in ("Aries", "Leo", "Acuario")
    ],
)


class _FakeCatalog:
    async def search(self, q: str, limit: int = 10):
        return SearchResult(
            query=q,
            count=1,
            truncated=False,
            stale=False,
            manifest=CatalogManifestDTO(
                version="v1",
                fetched_at="2026-08-31T00:00:00Z",
                product_count=1,
            ),
            results=[_DUO],
        )

    async def get_by_handle(self, handle: str):
        return _DUO


def _tool(tmp_path: Path) -> SetOrderSlotTool:
    return SetOrderSlotTool(
        workspace=tmp_path,
        vault_dir=tmp_path / "vault",
        catalog=_FakeCatalog(),
    )


def _ctx() -> ToolContext:
    return ToolContext(session_key="wa_vc", channel="whatsapp", chat_id="c")


async def _set(tool, **kwargs) -> dict:
    return json.loads(await tool.execute_with_context(_ctx(), **kwargs))


@pytest.mark.asyncio
async def test_color_validates_against_variant_colors_not_stale_tags(
    tmp_path,
):
    """"roja" se acepta (Aries es rojo) aunque los tags digan solo gris."""
    result = await _set(
        tool := _tool(tmp_path), producto="Duo Zodiacal", color="roja"
    )
    del tool
    assert result["updated"] is True
    assert result["order_draft"]["color"] == "rojo"
    assert "rejected" not in result


@pytest.mark.asyncio
async def test_color_only_hints_owning_signs(tmp_path):
    """Color sin signo → hint con el signo dueño para mostrarlo de una."""
    result = await _set(
        _tool(tmp_path), producto="Duo Zodiacal", color="roja"
    )
    assert result["signs_for_color"] == [
        {"value": "Aries", "colors": ["rojo"]}
    ]


@pytest.mark.asyncio
async def test_color_sign_mismatch_rejects_color_with_alternatives(tmp_path):
    """"Leo en rojo" no existe: Leo queda, el color se rechaza con las
    alternativas mismo-color-otro-signo y el color real del signo pedido."""
    result = await _set(
        _tool(tmp_path),
        producto="Duo Zodiacal",
        diseno="Leo",
        color="roja",
    )
    assert result["order_draft"]["diseno"] == "Leo"
    assert "color" not in result["order_draft"]
    (rejection,) = result["rejected"]
    assert rejection["field"] == "color"
    assert rejection["reason"] == "color_sign_mismatch"
    assert rejection["sign_colors"] == ["naranja"]
    assert rejection["same_color_signs"] == [
        {"value": "Aries", "colors": ["rojo"]}
    ]
    # El summary guía el guion: mismo color, otro signo, aclarándolo.
    assert "Aries" in result["summary"]
    assert "naranja" in result["summary"]


@pytest.mark.asyncio
async def test_diseno_newcomer_conflicts_with_draft_color(tmp_path):
    """Draft ya tiene color rojo; llega diseno=Leo → se rechaza el diseno
    (el recién llegado) con las mismas alternativas."""
    tool = _tool(tmp_path)
    await _set(tool, producto="Duo Zodiacal", color="rojo")
    result = await _set(tool, diseno="Leo")
    assert "diseno" not in result["order_draft"]
    assert result["order_draft"]["color"] == "rojo"
    (rejection,) = result["rejected"]
    assert rejection["field"] == "diseno"
    assert rejection["reason"] == "color_sign_mismatch"
    assert rejection["sign_colors"] == ["naranja"]
    assert rejection["same_color_signs"] == [
        {"value": "Aries", "colors": ["rojo"]}
    ]


@pytest.mark.asyncio
async def test_matching_pair_passes(tmp_path):
    result = await _set(
        _tool(tmp_path),
        producto="Duo Zodiacal",
        diseno="Aries",
        color="roja",
    )
    assert result["order_draft"]["diseno"] == "Aries"
    assert result["order_draft"]["color"] == "rojo"
    assert "rejected" not in result


@pytest.mark.asyncio
async def test_unknown_color_rejected_with_real_palette(tmp_path):
    """Color inexistente en el mapeo → rechazo closed-list con la paleta
    REAL de variantes (no los tags stale)."""
    result = await _set(
        _tool(tmp_path), producto="Duo Zodiacal", color="fucsia"
    )
    (rejection,) = result["rejected"]
    assert rejection["field"] == "color"
    assert rejection["available"] == ["rojo", "naranja", "azul claro"]
