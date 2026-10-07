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
        # Con foto y precio: lo que el catálogo de WhatsApp (Meta) tiene.
        thumbnail=f"https://img.test/{handle}.webp",
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


# ── Un solo mensaje cuando cabe (incidente 2026-10-06) ──────────────────────
# «¿Qué productos tienen?»: el bot mandó 29 handles por categoría y salieron
# TRES listas (10, 9 y 10). La tool cortaba cada categoría en 10 y paginaba
# de a 10 antes de saber cómo se iba a enviar; la lista de productos de Meta
# acepta hasta 30 productos en hasta 10 secciones en UN mensaje.


def _many(n: int, slug: str, label: str) -> list[CatalogProductDTO]:
    return [_product(f"{slug}-{i}", slug, label) for i in range(n)]


def _intents(tmp_path: Path) -> list[dict]:
    data = json.loads(
        (tmp_path / "isolated_vault" / "s_test" / "metadata.json").read_text(
            encoding="utf-8"
        )
    )
    return data["pending_ui_intents"]


async def _present(tmp_path: Path, products: list[CatalogProductDTO], handles: list[str]) -> dict:
    tool = PresentProductsTool(workspace=str(tmp_path), catalog=_FakeCatalog(products))
    out = await tool.execute_with_context(
        ToolContext(session_key="s_test", channel="whatsapp", chat_id="c"),
        handles=handles,
        intro_text="Este es nuestro catálogo:",
        group_by="categories",
    )
    return json.loads(out)


def _rows_by_section(tmp_path: Path) -> dict[str, int]:
    [intent] = _intents(tmp_path)
    return {s["title"]: len(s["rows"]) for s in intent["params"]["sections"]}


@pytest.mark.asyncio
async def test_29_products_in_3_categories_go_out_in_one_message(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("META_CATALOG_ID", "CAT_TEST")
    products = (
        _many(12, "velones", "Velones")
        + _many(10, "velas-religiosas", "Velas Religiosas")
        + _many(7, "aromaticas", "Aromáticas")
    )

    out = await _present(tmp_path, products, [p.handle for p in products])

    intents = _intents(tmp_path)
    assert len(intents) == 1, "un solo mensaje: la tool encola UN intent"
    assert sum(len(s["rows"]) for s in intents[0]["params"]["sections"]) == 29
    # La categoría de 12 conserva sus 12 (antes se cortaba en 10).
    assert _rows_by_section(tmp_path) == {"Velones": 12, "Velas Religiosas": 10, "Aromáticas": 7}
    assert out["count"] == 29
    # Lo que se le dice al LLM es la verdad: con el catálogo de Meta, un
    # mensaje (no «(en 3 mensajes)»); si WhatsApp lo rechaza, la lista.
    assert out["pages"] == 1
    assert "en un mensaje" in out["summary"]
    assert "(en 3 mensajes)" not in out["summary"]


@pytest.mark.asyncio
async def test_without_the_meta_catalog_the_llm_hears_how_many_lists_the_customer_gets(
    tmp_path: Path, monkeypatch
):
    """Sin el catálogo de Meta el respaldo es la lista de texto, que WhatsApp
    acepta de a 10 filas: el flush la parte y el aviso lo dice."""
    monkeypatch.delenv("META_CATALOG_ID", raising=False)
    products = _many(12, "velones", "Velones") + _many(13, "aromaticas", "Aromáticas")

    out = await _present(tmp_path, products, [p.handle for p in products])

    assert len(_intents(tmp_path)) == 1
    assert out["pages"] == 3
    assert "3 mensajes" in out["summary"]


@pytest.mark.asyncio
async def test_more_than_ten_categories_fit_in_ten_sections_without_losing_products(tmp_path: Path):
    products = [p for i in range(12) for p in _many(2, f"cat-{i:02d}", f"Cat {i:02d}")]

    out = await _present(tmp_path, products, [p.handle for p in products])

    by_section = _rows_by_section(tmp_path)
    assert len(by_section) == 10, "la lista de productos de Meta acepta hasta 10 secciones"
    assert sum(by_section.values()) == 24, "ningún producto se pierde en silencio"
    assert by_section["Otros"] == 6, "de la décima categoría en adelante van juntas en «Otros»"
    assert out["count"] == 24


@pytest.mark.asyncio
async def test_more_than_30_products_show_30_and_the_llm_knows_the_rest_did_not_go(tmp_path: Path):
    products = _many(33, "velones", "Velones")

    out = await _present(tmp_path, products, [p.handle for p in products])

    assert _rows_by_section(tmp_path) == {"Velones": 30}
    assert out["count"] == 30
    assert out["left_out"] == ["velones-30", "velones-31", "velones-32"]


@pytest.mark.asyncio
async def test_a_repeated_handle_is_one_row(tmp_path: Path):
    """WhatsApp rechaza una lista con dos filas del mismo id."""
    products = _many(5, "velones", "Velones")
    handles = [p.handle for p in products]

    out = await _present(tmp_path, products, handles + handles[:2])

    assert _rows_by_section(tmp_path) == {"Velones": 5}
    assert out["count"] == 5
