"""set_order_slot con mapeo color↔signo (Duo Zodiacal).

El mapeo (metadata `colores` del producto) dice en qué color salió la FOTO
de cada signo. Hasta 2026-09-22 se trataba como restricción ("Leo en rojo no
existe") y el bot le hacía elegir a la clienta entre su signo y su color. El
operador lo confirmó el 2026-09-23: la vela del Duo se hace en CUALQUIER color
de la paleta, en cualquier signo y sin costo extra. El contrato:

1. El color se valida contra los colores REALES de las variantes (los tags
   del producto están stale: "rojo" debe aceptarse aunque no haya tag).
2. Color distinto al de la foto del signo → se GUARDA, y el envelope avisa
   que la foto es solo referencia (`custom_color`) para que el bot confirme
   "te la hacemos en rojo" en vez de negarlo.
3. Color solo (sin signo) → se guarda + `signs_for_color` (qué foto muestra
   ese color, como referencia visual; el signo lo elige el cliente).
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
async def test_color_only_shows_which_photo_has_it_without_steering_sign(
    tmp_path,
):
    """Color sin signo → qué foto muestra ese color (referencia visual). El
    summary NO le pide al bot ofrecer ese signo: el signo es del cliente."""
    result = await _set(
        _tool(tmp_path), producto="Duo Zodiacal", color="roja"
    )
    assert result["signs_for_color"] == [
        {"value": "Aries", "colors": ["rojo"]}
    ]
    assert "ofrécele ese signo" not in result["summary"]


@pytest.mark.asyncio
async def test_sign_in_other_color_is_saved_as_custom_color(tmp_path):
    """"Leo en rojo" SÍ se hace (operador, 2026-09-23): se guardan los dos y
    el envelope avisa que la foto de Leo (naranja) es solo referencia."""
    result = await _set(
        _tool(tmp_path),
        producto="Duo Zodiacal",
        diseno="Leo",
        color="roja",
    )
    assert result["order_draft"]["diseno"] == "Leo"
    assert result["order_draft"]["color"] == "rojo"
    assert "rejected" not in result
    assert result["custom_color"] == {
        "sign": "Leo",
        "photo_colors": ["naranja"],
        "color": "rojo",
    }
    assert "referencia" in result["summary"]


@pytest.mark.asyncio
async def test_sign_after_color_keeps_both(tmp_path):
    """Draft ya tiene color rojo; llega diseno=Leo → quedan los dos (caso
    real: la clienta eligió Escorpio y pidió el color de SU foto)."""
    tool = _tool(tmp_path)
    await _set(tool, producto="Duo Zodiacal", color="rojo")
    result = await _set(tool, diseno="Leo")
    assert result["order_draft"]["diseno"] == "Leo"
    assert result["order_draft"]["color"] == "rojo"
    assert "rejected" not in result
    assert result["custom_color"]["photo_colors"] == ["naranja"]


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
