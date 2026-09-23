"""`set_order_slot` con varios productos: cada valor se valida contra SU producto.

Repro 2026-09-22 (ep_002): el borrador tenía la Trilogía del Terror y el bot
llamó `set_order_slot(diseno="Escorpio")` para el Duo Zodiacal. La tool validó
"Escorpio" contra la Trilogía (opción única "Unico") y lo rechazó tres veces:
el Duo nunca entró al pedido. Repro orden #31 (2026-09-16): el color del Velón
pisó el del Duo.
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

_TRILOGIA = CatalogProductDTO(
    id="prod_trilogia",
    handle="trilogia-del-terror",
    title="Trilogía del Terror",
    status="published",
    tags=["Aroma: Frutos rojos", "Color: Blanco"],
    options={"Unico": ["Unico"]},
)
_DUO = CatalogProductDTO(
    id="prod_duo",
    handle="duo-zodiacal",
    title="Duo Zodiacal",
    status="published",
    tags=["Aroma: Frutos rojos", "Aroma: Lavanda", "Color: Blanco", "Color: Morado"],
    options={"Signo": ["Aries", "Leo", "Escorpio"]},
)
_VELON = CatalogProductDTO(
    id="prod_velon",
    handle="velon-amor-eterno",
    title="Velón Amor Eterno",
    status="published",
    tags=["Aroma: Lavanda", "Color: verde", "Color: Blanco"],
)


class _FakeCatalog:
    async def search(self, q: str, *, limit: int = 10) -> SearchResult:
        return SearchResult(
            query=q,
            count=3,
            truncated=False,
            stale=False,
            manifest=CatalogManifestDTO(
                version="v1", fetched_at="2026-09-23T00:00:00Z", product_count=3
            ),
            results=[_TRILOGIA, _DUO, _VELON],
        )


def _ctx() -> ToolContext:
    return ToolContext(session_key="wa_multi", channel="whatsapp", chat_id="c")


def _tool(tmp_path: Path) -> SetOrderSlotTool:
    return SetOrderSlotTool(
        workspace=tmp_path, vault_dir=tmp_path / "vault", catalog=_FakeCatalog()
    )


async def _set(tool: SetOrderSlotTool, **kwargs) -> dict:
    return json.loads(await tool.execute_with_context(_ctx(), **kwargs))


def _items(tmp_path: Path) -> list[dict]:
    meta = json.loads(
        (tmp_path / "vault" / "wa_multi" / "metadata.json").read_text(encoding="utf-8")
    )
    return draft_items(meta["episodes"][-1]["order_draft"])


@pytest.mark.asyncio
async def test_sign_for_a_second_product_validates_against_that_product(tmp_path):
    tool = _tool(tmp_path)
    await _set(tool, producto="Trilogía del Terror")

    result = await _set(tool, producto="Duo Zodiacal", diseno="Escorpio")

    assert "rejected" not in result
    assert _items(tmp_path) == [
        {"producto": "Trilogía del Terror"},
        {"producto": "Duo Zodiacal", "diseno": "Escorpio"},
    ]


@pytest.mark.asyncio
async def test_value_without_product_goes_to_the_item_it_belongs_to(tmp_path):
    """Pedido con los dos productos y el bot manda solo el signo: "Escorpio"
    solo existe en el Duo → va al Duo, aunque la Trilogía sea la última
    tocada."""
    tool = _tool(tmp_path)
    await _set(tool, producto="Duo Zodiacal")
    await _set(tool, producto="Trilogía del Terror", cantidad="1")

    result = await _set(tool, diseno="Escorpio")

    assert "rejected" not in result
    assert _items(tmp_path) == [
        {"producto": "Duo Zodiacal", "diseno": "Escorpio"},
        {"producto": "Trilogía del Terror", "cantidad": "1"},
    ]


@pytest.mark.asyncio
async def test_sign_of_a_product_not_in_the_order_says_which_product_it_is(tmp_path):
    """El run exacto: solo la Trilogía en el borrador + `diseno="Escorpio"`.
    Se rechaza, pero diciendo de QUÉ producto es para que el bot lo agregue."""
    tool = _tool(tmp_path)
    await _set(tool, producto="Trilogía del Terror")

    result = await _set(tool, diseno="Escorpio")

    (rejection,) = result["rejected"]
    assert rejection["belongs_to"] == ["Duo Zodiacal"]
    assert "producto='Duo Zodiacal'" in result["summary"]
    assert _items(tmp_path) == [{"producto": "Trilogía del Terror"}]


@pytest.mark.asyncio
async def test_color_of_second_product_does_not_overwrite_the_first(tmp_path):
    """Orden #31: "verde" del Velón pisaba el "Morado" del Duo."""
    tool = _tool(tmp_path)
    await _set(tool, producto="Duo Zodiacal", color="morado", diseno="Aries")

    await _set(tool, producto="Velón Amor Eterno", color="verde")

    assert _items(tmp_path) == [
        {"producto": "Duo Zodiacal", "color": "Morado", "diseno": "Aries"},
        {"producto": "Velón Amor Eterno", "color": "verde"},
    ]


@pytest.mark.asyncio
async def test_handle_and_title_of_the_same_product_are_one_item(tmp_path):
    """Handle o título → el MISMO ítem (se reconoce por el catálogo). Se
    conserva como se escribió primero: `session-actions@v1` (MBA) manda el
    handle y lo lee de vuelta tal cual."""
    tool = _tool(tmp_path)
    await _set(tool, producto="duo-zodiacal")

    await _set(tool, producto="Duo Zodiacal", diseno="Leo")

    assert _items(tmp_path) == [{"producto": "duo-zodiacal", "diseno": "Leo"}]


@pytest.mark.asyncio
async def test_quitar_drops_the_product_from_the_order(tmp_path):
    tool = _tool(tmp_path)
    await _set(tool, producto="Duo Zodiacal")
    await _set(tool, producto="Velón Amor Eterno")

    result = await _set(tool, producto="Velón Amor Eterno", quitar=True)

    assert result["updated"] is True
    assert _items(tmp_path) == [{"producto": "Duo Zodiacal"}]


@pytest.mark.asyncio
async def test_envelope_shows_every_product_of_the_order(tmp_path):
    tool = _tool(tmp_path)
    await _set(tool, producto="Trilogía del Terror")

    result = await _set(tool, producto="Duo Zodiacal", diseno="Escorpio")

    assert result["order_draft"]["items"] == [
        {"producto": "Trilogía del Terror"},
        {"producto": "Duo Zodiacal", "diseno": "Escorpio"},
    ]


def test_trace_marks_a_fully_rejected_slot_call_as_failed():
    """En el run, los 3 `set_order_slot(diseno="Escorpio")` rechazados se
    trazaron `ok=True` con la nota `rejected_slots:None`: el scorecard no
    podía ver que el signo nunca entró. Un rechazo total es un fallo, y la
    nota nombra el campo."""
    from src.plugins.chats.agent.sales.turn_trace import summarize_tool_event

    envelope = {
        "updated": False,
        "captured": {},
        "rejected": [
            {"field": "diseno", "given": "Escorpio", "available": ["Unico"]}
        ],
    }

    event = summarize_tool_event(
        "set_order_slot", {"diseno": "Escorpio"}, json.dumps(envelope)
    )

    assert event["ok"] is False
    assert event["error"] == "slots_rejected"
    assert "rejected_slots:diseno" in event["notes"]


def test_scripts_teach_one_item_per_product():
    ws = (
        Path(__file__).resolve().parents[4]
        / "src/plugins/chats/agent/sales/workspace"
    )
    tools = (ws / "TOOLS.md").read_text(encoding="utf-8")
    variantes = (ws / "skills/etapa_variantes/SKILL.md").read_text(encoding="utf-8")

    assert "un ítem por producto" in tools
    assert "quitar=true" in tools
    assert "de CADA producto" in variantes
