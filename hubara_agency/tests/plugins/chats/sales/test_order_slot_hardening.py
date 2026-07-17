"""set_order_slot endurecido — incidente 2026-07-17 (run 019f6db3).

Dos modos de fallo reales del mismo turno:

1. **notas se sobrescribía**: el LLM (ciego post-deploy) llamó
   `set_order_slot(notas="...Pendiente saber a qué dos se refiere...")` y
   PISÓ las notas de la tarde que contenían los signos elegidos ("1× Leo
   café + 1× Libra sándalo") — destruyó el único registro del pedido antes
   de leerlo. Nuevo contrato: `notas` APPENDEA (memoria acumulativa);
   string vacío limpia; valor idéntico no duplica.

2. **el signo no tenía slot estructurado** (diferido del premortem #178,
   mordió a las 48h): vivía solo en notas texto libre. Nuevo slot `diseno`
   validado closed-list contra los option values del producto (mismo
   patrón que aroma/color: match accent-insensible, multi-valor por coma,
   rechazo con `available`).
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
from src.plugins.chats.agent.sales.use_cases.order_draft import (
    build_order_draft_note,
)

_DUO = CatalogProductDTO(
    id="prod_duo",
    handle="duo-zodiacal",
    title="Duo Zodiacal",
    status="published",
    options={"Signo": ["Leo", "Libra", "Geminis"]},
    variants=[
        CatalogVariantDTO(
            id=f"v_{s.lower()}",
            title=s,
            options={"Signo": s},
            prices=[CatalogPriceDTO(amount="35000", currency_code="cop")],
        )
        for s in ("Leo", "Libra", "Geminis")
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
                fetched_at="2026-07-17T00:00:00Z",
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
    return ToolContext(session_key="wa_h", channel="whatsapp", chat_id="c")


async def _set(tool, **kwargs) -> dict:
    return json.loads(await tool.execute_with_context(_ctx(), **kwargs))


# ---------- notas append ----------


@pytest.mark.asyncio
async def test_notas_appends_instead_of_overwriting(tmp_path):
    tool = _tool(tmp_path)
    await _set(tool, notas="1× Leo café + 1× Libra sándalo")
    result = await _set(tool, notas="retomando desde remarketing")
    notas = result["order_draft"]["notas"]
    assert "1× Leo café + 1× Libra sándalo" in notas
    assert "retomando desde remarketing" in notas


@pytest.mark.asyncio
async def test_notas_identical_value_does_not_duplicate(tmp_path):
    tool = _tool(tmp_path)
    await _set(tool, notas="1× Leo café")
    result = await _set(tool, notas="1× Leo café")
    assert result["order_draft"]["notas"].count("1× Leo café") == 1


@pytest.mark.asyncio
async def test_notas_empty_string_still_clears(tmp_path):
    tool = _tool(tmp_path)
    await _set(tool, notas="algo viejo")
    result = await _set(tool, notas="", cantidad="2")
    assert "notas" not in result["order_draft"]


# ---------- slot diseno ----------


@pytest.mark.asyncio
async def test_diseno_valid_multi_value_canonical(tmp_path):
    tool = _tool(tmp_path)
    result = await _set(
        tool, producto="Duo Zodiacal", diseno="leo, GÉMINIS"
    )
    assert result["order_draft"]["diseno"] == "Leo, Geminis"


@pytest.mark.asyncio
async def test_diseno_invalid_rejected_with_available(tmp_path):
    tool = _tool(tmp_path)
    result = await _set(tool, producto="Duo Zodiacal", diseno="Patito")
    assert "diseno" not in result["order_draft"]
    rejected = {r["field"]: r for r in result.get("rejected", [])}
    assert "diseno" in rejected
    assert "Leo" in rejected["diseno"]["available"]


@pytest.mark.asyncio
async def test_draft_note_labels_diseno(tmp_path):
    note = build_order_draft_note(
        {"producto": "Duo Zodiacal", "diseno": "Leo, Libra"}
    )
    assert "Diseño/Signo: Leo, Libra" in note
