"""El título de sección de la lista de WhatsApp es el NOMBRE de la categoría.

`group_by="categories"` usaba `product.categories[0]` — el slug — así que el
cliente veía "velas-religiosas" como encabezado en la lista.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog import ProductNotFoundError
from src.platform.catalog.dtos import (
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
)
from src.plugins.chats.agent.sales.tools.ui_intents import PresentProductsTool


def _product(handle: str, slug: str, label: str | None) -> CatalogProductDTO:
    return CatalogProductDTO(
        id=f"prod_{handle}",
        handle=handle,
        title=handle.replace("-", " ").title(),
        status="published",
        categories=[slug],
        category_labels={slug: label} if label else None,
        variants=[
            CatalogVariantDTO(
                id=f"v_{handle}",
                title="Unico",
                prices=[CatalogPriceDTO(amount="23000", currency_code="cop")],
            )
        ],
    )


class _FakeCatalog:
    def __init__(self, products: list[CatalogProductDTO]) -> None:
        self._by_handle = {p.handle: p for p in products}

    async def get_by_handle(self, handle: str):
        if handle not in self._by_handle:
            raise ProductNotFoundError(handle)
        return self._by_handle[handle]


def _sections(tmp_path: Path) -> list[dict]:
    data = json.loads(
        (tmp_path / "isolated_vault" / "s_test" / "metadata.json").read_text(
            encoding="utf-8"
        )
    )
    return data["pending_ui_intents"][0]["params"]["sections"]


@pytest.mark.asyncio
async def test_section_title_uses_category_name_not_slug(tmp_path: Path):
    tool = PresentProductsTool(
        workspace=str(tmp_path),
        catalog=_FakeCatalog(
            [
                _product("corona", "velas-religiosas", "Velas Religiosas"),
                _product("luz-serena", "velas-aromaticas", "Velas Aromáticas"),
            ]
        ),
    )
    await tool.execute_with_context(
        ToolContext(session_key="s_test", channel="whatsapp", chat_id="c"),
        handles=["corona", "luz-serena"],
        intro_text="Catálogo:",
        group_by="categories",
    )
    titles = [s["title"] for s in _sections(tmp_path)]
    assert titles == ["Velas Religiosas", "Velas Aromáticas"]


@pytest.mark.asyncio
async def test_section_title_deslugifies_old_snapshots(tmp_path: Path):
    tool = PresentProductsTool(
        workspace=str(tmp_path),
        catalog=_FakeCatalog([_product("corona", "velas-religiosas", None)]),
    )
    await tool.execute_with_context(
        ToolContext(session_key="s_test", channel="whatsapp", chat_id="c"),
        handles=["corona"],
        intro_text="Catálogo:",
        group_by="categories",
    )
    assert [s["title"] for s in _sections(tmp_path)] == ["Velas Religiosas"]
