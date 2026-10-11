"""`set_order_slot(lineas=...)`: un producto en varias variantes, cada una con
su cantidad.

Laboratorio, caso 4567 (turno 22, producción y los dos bots): «Mejor 2, una
lila y otra azul» quedó como `cantidad=2`, `color=Lila` y
`notas="Segunda unidad en azul"`, y la confirmación salió con dos lilas. Con
`lineas` el bot guarda una línea por variante; los valores se validan contra
el producto como cualquier otro, y si una línea no sirve no se escribe
ninguna (no queda un reparto a medias).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog.dtos import (
    CatalogManifestDTO,
    CatalogProductDTO,
    SearchResult,
)
from src.plugins.chats.agent.sales.tools.order_draft import SetOrderSlotTool
from src.plugins.chats.shared.draft_items import draft_items

GORRION = "Velón Gorrión"
_GORRION = CatalogProductDTO(
    id="prod_gorrion",
    handle="velon-gorrion",
    title=GORRION,
    status="published",
    tags=[
        "Aroma: Lavanda",
        "Aroma: Limoncillo",
        "Color: Lila",
        "Color: Azul",
        "Color: Rosado",
    ],
    options={"Unico": ["Unico"]},
)
_DUO = CatalogProductDTO(
    id="prod_duo",
    handle="duo-zodiacal",
    title="Duo Zodiacal",
    status="published",
    # Dos opciones de cada una: el caso es de ruteo (la opción única se
    # anota sola: test_order_slot_single_option.py).
    tags=["Aroma: Lavanda", "Aroma: Coco", "Color: Blanco", "Color: Negro"],
    options={"Signo": ["Aries", "Leo", "Escorpio"]},
)


class _FakeCatalog:
    async def search(self, q: str, *, limit: int = 10) -> SearchResult:
        return SearchResult(
            query=q,
            count=2,
            truncated=False,
            stale=False,
            manifest=CatalogManifestDTO(
                version="v1", fetched_at="2026-09-30T00:00:00Z", product_count=2
            ),
            results=[_GORRION, _DUO],
        )


def _ctx() -> ToolContext:
    return ToolContext(session_key="wa_lines", channel="whatsapp", chat_id="c")


def _tool(tmp_path: Path) -> SetOrderSlotTool:
    return SetOrderSlotTool(
        workspace=tmp_path, vault_dir=tmp_path / "vault", catalog=_FakeCatalog()
    )


async def _set(tool: SetOrderSlotTool, **kwargs) -> dict:
    return json.loads(await tool.execute_with_context(_ctx(), **kwargs))


def _meta(tmp_path: Path) -> dict:
    return json.loads(
        (tmp_path / "vault" / "wa_lines" / "metadata.json").read_text(encoding="utf-8")
    )


def _items(tmp_path: Path) -> list[dict]:
    return draft_items(_meta(tmp_path)["episodes"][-1]["order_draft"])


async def _lila_x2(tool: SetOrderSlotTool) -> None:
    """El borrador del turno 22 antes de repartir."""
    await _set(tool, producto=GORRION, aroma="Lavanda", color="Lila", cantidad="2")


SPLIT = [
    {"producto": GORRION, "aroma": "Lavanda", "color": "Lila", "cantidad": "1"},
    {"producto": GORRION, "aroma": "Lavanda", "color": "Azul", "cantidad": "1"},
]


@pytest.mark.asyncio
async def test_lines_split_the_product_one_line_per_variant(tmp_path):
    tool = _tool(tmp_path)
    await _lila_x2(tool)

    result = await _set(
        tool,
        producto=GORRION,
        lineas=[{"color": "lila", "cantidad": "1"}, {"color": "Azul", "cantidad": "1"}],
    )

    assert _items(tmp_path) == SPLIT
    assert result["updated"] is True and "rejected" not in result
    assert result["captured"]["lineas"] == [
        {"color": "Lila", "cantidad": "1"},
        {"color": "Azul", "cantidad": "1"},
    ]
    assert f"{GORRION} quedó en 2 líneas" in result["summary"]


@pytest.mark.asyncio
async def test_lines_arrive_as_text_too_and_go_to_the_product_in_progress(tmp_path):
    """Algunos proveedores mandan el arreglo como texto JSON."""
    tool = _tool(tmp_path)
    await _lila_x2(tool)

    await _set(tool, lineas=json.dumps([{"color": "Lila", "cantidad": 1}, {"color": "Azul", "cantidad": 1}]))

    assert _items(tmp_path) == SPLIT


@pytest.mark.asyncio
async def test_shared_values_and_order_data_travel_in_the_same_call(tmp_path):
    tool = _tool(tmp_path)

    await _set(
        tool,
        producto=GORRION,
        aroma="Lavanda",
        lineas=[{"color": "Lila", "cantidad": "1"}, {"color": "Azul", "cantidad": "1"}],
        ciudad="Chía",
    )

    assert _items(tmp_path) == SPLIT
    assert _meta(tmp_path)["episodes"][-1]["order_draft"]["slots"]["ciudad"] == "Chía"


@pytest.mark.asyncio
async def test_a_line_the_product_does_not_have_writes_nothing(tmp_path):
    tool = _tool(tmp_path)
    await _lila_x2(tool)

    result = await _set(
        tool,
        producto=GORRION,
        lineas=[{"color": "Lila", "cantidad": "1"}, {"color": "Verde", "cantidad": "1"}],
    )

    assert result["updated"] is False
    (rejection,) = result["rejected"]
    assert (rejection["field"], rejection["given"]) == ("color", "Verde")
    assert "Azul" in rejection["available"]
    assert _items(tmp_path) == [
        {"producto": GORRION, "aroma": "Lavanda", "color": "Lila", "cantidad": "2"}
    ]


@pytest.mark.asyncio
async def test_a_line_without_a_quantity_writes_nothing(tmp_path):
    tool = _tool(tmp_path)
    await _lila_x2(tool)

    result = await _set(
        tool, producto=GORRION, lineas=[{"color": "Lila", "cantidad": "1"}, {"color": "Azul"}]
    )

    assert result["updated"] is False
    assert result["rejected"][0]["reason"] == "invalid_lines"
    assert "cantidad" in result["summary"]
    assert len(_items(tmp_path)) == 1


@pytest.mark.asyncio
async def test_changing_what_tells_the_lines_apart_needs_the_lines(tmp_path):
    tool = _tool(tmp_path)
    await _lila_x2(tool)
    await _set(tool, producto=GORRION, lineas=[{"color": "Lila", "cantidad": "1"}, {"color": "Azul", "cantidad": "1"}])

    result = await _set(tool, producto=GORRION, color="Rosado", cantidad="3")

    assert result["updated"] is False
    assert [(r["field"], r["reason"]) for r in result["rejected"]] == [
        ("color", "split_lines"),
        ("cantidad", "split_lines"),
    ]
    assert "va en 2 líneas" in result["summary"] and "`lineas`" in result["summary"]
    assert "1× Lila" in result["summary"] and "1× Azul" in result["summary"]
    assert _items(tmp_path) == SPLIT


@pytest.mark.asyncio
async def test_what_all_lines_share_changes_in_all_of_them(tmp_path):
    tool = _tool(tmp_path)
    await _lila_x2(tool)
    await _set(tool, producto=GORRION, lineas=[{"color": "Lila", "cantidad": "1"}, {"color": "Azul", "cantidad": "1"}])

    result = await _set(tool, aroma="limoncillo", cantidad="2")

    assert "rejected" not in result
    assert [(i["aroma"], i["cantidad"]) for i in _items(tmp_path)] == [("Limoncillo", "1"), ("Limoncillo", "1")]


@pytest.mark.asyncio
async def test_a_value_of_another_product_goes_there_with_a_split_product_in_the_order(tmp_path):
    tool = _tool(tmp_path)
    await _set(tool, producto="Duo Zodiacal")
    await _set(tool, producto=GORRION, aroma="Lavanda", lineas=[{"color": "Lila", "cantidad": "1"}, {"color": "Azul", "cantidad": "1"}])

    result = await _set(tool, diseno="Leo")

    assert "rejected" not in result
    assert _items(tmp_path) == [{"producto": "Duo Zodiacal", "diseno": "Leo"}, *SPLIT]


@pytest.mark.asyncio
async def test_removing_a_split_product_drops_all_its_lines(tmp_path):
    tool = _tool(tmp_path)
    await _set(tool, producto="Duo Zodiacal")
    await _set(tool, producto=GORRION, lineas=[{"color": "Lila", "cantidad": "1"}, {"color": "Azul", "cantidad": "1"}])

    result = await _set(tool, producto=GORRION, quitar=True)

    assert result["removed"] == GORRION
    assert _items(tmp_path) == [{"producto": "Duo Zodiacal"}]


def test_the_schema_offers_lines_with_their_quantity():
    lines = SetOrderSlotTool.parameters["properties"]["lineas"]

    assert lines["type"] == "array"
    assert lines["items"]["required"] == ["cantidad"]
    assert set(lines["items"]["properties"]) == {"color", "aroma", "diseno", "cantidad"}
    assert "una lila y otra azul" in lines["description"]
    assert "`lineas`" in SetOrderSlotTool.description


def test_scripts_teach_one_line_per_variant():
    """El bot aprende `lineas` donde aprende los ítems (memoria del pedido y
    etapa de variantes). El cierre no necesita más prompt: la nota del turno
    pide una línea por cada una y `split_lines_mismatch` lo hace cumplir (el
    presupuesto del prompt manda las reglas duras a la mecánica)."""
    ws = Path(__file__).resolve().parents[4] / "src/plugins/chats/agent/sales/workspace"
    tools = (ws / "TOOLS.md").read_text(encoding="utf-8")
    variantes = (ws / "skills/etapa_variantes/SKILL.md").read_text(encoding="utf-8")

    assert "un producto en varias variantes = `lineas`" in tools
    assert "lineas=" in variantes and "una lila y otra azul" in variantes
