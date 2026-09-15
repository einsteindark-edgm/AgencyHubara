"""Contexto de catálogo del scorecard (HU-SC-1)."""
from __future__ import annotations

from dataclasses import dataclass, field

from src.plugins.chats.agent.sales_eval.scorecard.catalog_context import build_check_context


@dataclass
class _P:
    title: str
    handle: str
    tags: list[str] = field(default_factory=list)
    variants: list = field(default_factory=list)


@dataclass
class _R:
    results: list


class _Catalog:
    async def search(self, q: str = "", limit: int = 30):
        return _R([
            _P("Cubo Love", "cubo-love", ["Aroma: Café", "Aroma: Lavanda", "Color: Azul"]),
            _P("Vela Ángel", "vela-angel", ["Aroma: Lavanda", "Color: Blanco"]),
        ])


class _Broken:
    async def search(self, q: str = "", limit: int = 30):
        raise RuntimeError("snapshot caído")


async def test_context_collects_deduped_aromas_colors_and_titles() -> None:
    ctx = await build_check_context(_Catalog())

    assert ctx.catalog_available is True
    assert ctx.aromas == ("Café", "Lavanda")
    assert ctx.colors == ("Azul", "Blanco")
    assert ctx.product_titles == ("Cubo Love", "Vela Ángel")
    assert "Cubo Love" in ctx.catalog_summary


async def test_context_without_catalog_is_marked_unavailable() -> None:
    ctx = await build_check_context(_Broken())

    assert ctx.catalog_available is False
    assert ctx.aromas == ()
