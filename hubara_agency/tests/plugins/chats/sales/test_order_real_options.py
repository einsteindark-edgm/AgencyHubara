"""Las opciones reales de Medusa se eligen antes de cerrar (2026-10-14, ···0686).

Las Velas de deseos tienen en Medusa la opción «Color» (Rojo y verde,
Multicolor) y una sola etiqueta, «Aroma: Navidad». El bot preguntó «¿rojo y
verde o multicolor?», la clienta contestó por el envío y el pago, y
`set_order_slot` anotó las 2 cajas con el aroma solo y el color marcado «sin
opciones»: `offered_options` miraba solo las etiquetas. El pedido quedó
«completo», la etapa saltó a los datos de envío y se registró sin color
(Medusa cayó a la primera variante); un colega tuvo que preguntárselo.

Es un hecho del catálogo, no una decisión (como la opción única): una opción
de Medusa con dos valores o más se elige. `set_order_slot` la anota en el
borrador (`por_elegir`) y la etapa no sale de variantes hasta que el color o
el diseño del ítem tenga uno de sus valores (el modelo a veces la anota como
`diseno`: pedido 78). El color se valida contra esa opción si no hay
etiquetas. El Duo Zodiacal (opción «Signo») espera el signo igual.
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
from src.plugins.chats.shared.draft_items import draft_items, missing_variants

DESEOS = CatalogProductDTO(
    id="prod_deseos", handle="velas-de-deseos-cuadradas", title="Velas de deseos cuadradas", status="published",
    tags=["Aroma: Navidad"], options={"Color": ["Rojo y verde", "Multicolor"]},
)
DUO = CatalogProductDTO(
    id="prod_duo", handle="duo-zodiacal", title="Duo Zodiacal", status="published",
    tags=["Aroma: Frutos rojos"], options={"Signo": ["Aries", "Leo"]},
    metadata={"colores": "Aries: Rojo; Leo: Dorado"},
)
TRILOGIA = CatalogProductDTO(
    id="prod_trilogia", handle="trilogia-del-terror", title="Trilogía del Terror", status="published",
    tags=["Aroma: Frutos rojos", "Color: Blanco"], options={"Unico": ["Unico"]},
)
SID = "wa_options"


class _Catalog:
    async def search(self, q: str, *, limit: int = 10) -> SearchResult:
        products = [DESEOS, DUO, TRILOGIA]
        return SearchResult(
            query=q, count=len(products), truncated=False, stale=False,
            manifest=CatalogManifestDTO(version="v1", fetched_at="2026-10-14T00:00:00Z", product_count=3),
            results=products,
        )


def _tool(tmp_path: Path) -> SetOrderSlotTool:
    return SetOrderSlotTool(workspace=tmp_path, vault_dir=tmp_path / "vault", catalog=_Catalog())


async def _set(tool: SetOrderSlotTool, **kwargs) -> dict:
    ctx = ToolContext(session_key=SID, channel="whatsapp", chat_id="c")
    return json.loads(await tool.execute_with_context(ctx, **kwargs))


def _meta(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "vault" / SID / "metadata.json").read_text(encoding="utf-8"))


def _draft(tmp_path: Path) -> dict:
    return _meta(tmp_path)["episodes"][-1]["order_draft"]


@pytest.mark.asyncio
async def test_the_incident_the_color_option_of_medusa_is_still_to_choose(tmp_path: Path) -> None:
    result = await _set(_tool(tmp_path), producto="Velas de deseos cuadradas", cantidad="2")

    (item,) = draft_items(_draft(tmp_path))
    assert item["aroma"] == "Navidad" and not item.get("color")
    assert resolve_funnel_stage(_meta(tmp_path)) == STAGE_VARIANTES
    assert "color" not in (_draft(tmp_path).get("sin_opciones") or {}).get("velas de deseos cuadradas", [])
    # El modelo se entera en la misma llamada de lo que falta escoger.
    assert "Rojo y verde" in result["summary"] and "Multicolor" in result["summary"]


@pytest.mark.parametrize(
    ("kwargs", "chosen"),
    [
        ({"color": "multicolor"}, "Multicolor"),
        # Pedido 78: el modelo anotó la opción de Medusa como diseño.
        ({"diseno": "Rojo y verde"}, "Rojo y verde"),
    ],
)
@pytest.mark.asyncio
async def test_choosing_a_value_of_the_option_moves_to_shipping(tmp_path: Path, kwargs: dict, chosen: str) -> None:
    tool = _tool(tmp_path)
    await _set(tool, producto="Velas de deseos cuadradas", cantidad="2")

    await _set(tool, producto="Velas de deseos cuadradas", **kwargs)

    (item,) = draft_items(_draft(tmp_path))
    assert chosen in (item.get("color"), item.get("diseno"))
    assert resolve_funnel_stage(_meta(tmp_path)) == STAGE_DATOS_ENVIO


@pytest.mark.asyncio
async def test_a_color_the_option_does_not_have_is_rejected(tmp_path: Path) -> None:
    tool = _tool(tmp_path)
    await _set(tool, producto="Velas de deseos cuadradas", cantidad="2")

    result = await _set(tool, producto="Velas de deseos cuadradas", color="azul")

    assert result.get("rejected") and not draft_items(_draft(tmp_path))[0].get("color")
    assert resolve_funnel_stage(_meta(tmp_path)) == STAGE_VARIANTES


@pytest.mark.asyncio
async def test_a_mark_written_before_the_fix_is_corrected_on_the_next_call(tmp_path: Path) -> None:
    """Los borradores en curso al desplegar traen `sin_opciones: color`."""
    tool = _tool(tmp_path)
    await _set(tool, producto="Velas de deseos cuadradas", cantidad="2")
    meta = _meta(tmp_path)
    draft = meta["episodes"][-1]["order_draft"]
    draft["sin_opciones"] = {"velas de deseos cuadradas": ["color"], "velas-de-deseos-cuadradas": ["color"]}
    draft.pop("por_elegir", None)
    (tmp_path / "vault" / SID / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

    await _set(tool, producto="Velas de deseos cuadradas", cantidad="2")

    assert resolve_funnel_stage(_meta(tmp_path)) == STAGE_VARIANTES


@pytest.mark.asyncio
async def test_the_duo_waits_for_the_sign(tmp_path: Path) -> None:
    tool = _tool(tmp_path)
    await _set(tool, producto="Duo Zodiacal", cantidad="1", color="Dorado")
    assert resolve_funnel_stage(_meta(tmp_path)) == STAGE_VARIANTES

    await _set(tool, producto="Duo Zodiacal", diseno="Leo")

    assert resolve_funnel_stage(_meta(tmp_path)) == STAGE_DATOS_ENVIO


@pytest.mark.asyncio
async def test_a_product_without_real_options_does_not_wait(tmp_path: Path) -> None:
    await _set(_tool(tmp_path), producto="Trilogía del Terror", cantidad="1")

    assert resolve_funnel_stage(_meta(tmp_path)) == STAGE_DATOS_ENVIO


def test_a_draft_from_before_the_fix_reads_as_before() -> None:
    """Sin `por_elegir` (borradores viejos), la etapa se lee como siempre."""
    item = {"producto": "Velas de deseos cuadradas", "aroma": "Navidad", "cantidad": "2"}
    draft = {"items": [item], "sin_opciones": {"velas de deseos cuadradas": ["color"]}}

    assert missing_variants(draft, item) == []
