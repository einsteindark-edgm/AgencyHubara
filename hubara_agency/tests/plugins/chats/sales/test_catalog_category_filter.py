"""El agente filtra por categoría de forma determinista (no por adivinanza).

Se ejercita contra el snapshot REAL (LocalSnapshotCatalogClient) — sin mocks:
lo que falla acá falla en la conversación.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient
from src.plugins.chats.agent.sales.tools.catalog import (
    ListCategoriesTool,
    SearchProductsTool,
)

CTX = ToolContext(session_key="s", channel="whatsapp", chat_id="c")


def _product(pid: str, handle: str, title: str, slug: str, label: str) -> dict:
    return {
        "id": pid,
        "handle": handle,
        "title": title,
        "status": "published",
        "categories": [slug],
        "category_labels": {slug: label},
        "variants": [
            {
                "id": f"v{pid}",
                "title": "Unico",
                "prices": [{"amount": "49000", "currency_code": "cop"}],
            }
        ],
    }


@pytest.fixture
def catalog(tmp_path: Path) -> LocalSnapshotCatalogClient:
    (tmp_path / "snapshot.json").write_text(
        json.dumps(
            [
                _product("1", "corona", "Corona de Redención",
                         "velas-religiosas", "Velas Religiosas"),
                _product("2", "cruz-de-vida", "Cruz de Vida",
                         "velas-religiosas", "Velas Religiosas"),
                _product("3", "luz-serena", "Luz Serena",
                         "velas-aromaticas", "Velas Aromáticas"),
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "version": "v1",
                "fetched_at": "2099-01-01T00:00:00+00:00",
                "product_count": 3,
            }
        )
    )
    return LocalSnapshotCatalogClient(tmp_path)


@pytest.mark.asyncio
async def test_search_with_category_returns_only_that_category(catalog):
    tool = SearchProductsTool(workspace=".", catalog=catalog)
    payload = json.loads(
        await tool.execute_with_context(CTX, q="", category="religiosas")
    )
    assert [r["handle"] for r in payload["results"]] == ["corona", "cruz-de-vida"]
    assert payload["category"]["matched"] == "Velas Religiosas"


@pytest.mark.asyncio
async def test_search_with_category_typo_still_resolves(catalog):
    tool = SearchProductsTool(workspace=".", catalog=catalog)
    payload = json.loads(
        await tool.execute_with_context(CTX, q="", category="velas religosas")
    )
    assert [r["handle"] for r in payload["results"]] == ["corona", "cruz-de-vida"]


@pytest.mark.asyncio
async def test_unknown_category_returns_the_closed_list_not_a_denial(catalog):
    tool = SearchProductsTool(workspace=".", catalog=catalog)
    payload = json.loads(
        await tool.execute_with_context(CTX, q="", category="zapatos")
    )
    assert payload["results"] == []
    assert payload["category"]["matched"] is None
    assert payload["category"]["available"] == [
        "Velas Aromáticas",
        "Velas Religiosas",
    ]


@pytest.mark.asyncio
async def test_ambiguous_category_offers_candidates(catalog):
    tool = SearchProductsTool(workspace=".", catalog=catalog)
    payload = json.loads(
        await tool.execute_with_context(CTX, q="", category="velas")
    )
    assert payload["category"]["matched"] is None
    assert payload["category"]["candidates"] == [
        "Velas Aromáticas",
        "Velas Religiosas",
    ]


@pytest.mark.asyncio
async def test_summary_carries_the_category_labels(catalog):
    tool = SearchProductsTool(workspace=".", catalog=catalog)
    payload = json.loads(await tool.execute_with_context(CTX, q="corona"))
    assert payload["results"][0]["categories"] == ["Velas Religiosas"]


@pytest.mark.asyncio
async def test_list_categories_tool_returns_closed_list_with_counts(catalog):
    tool = ListCategoriesTool(workspace=".", catalog=catalog)
    payload = json.loads(await tool.execute_with_context(CTX))
    assert payload["categories"] == [
        {"name": "Velas Aromáticas", "product_count": 1},
        {"name": "Velas Religiosas", "product_count": 2},
    ]


@pytest.mark.asyncio
async def test_no_categories_in_catalog_is_reported_as_text_fallback(tmp_path: Path):
    (tmp_path / "snapshot.json").write_text(
        json.dumps(
            [
                {
                    "id": "1",
                    "handle": "corona",
                    "title": "Corona Religiosa",
                    "status": "published",
                }
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "version": "v1",
                "fetched_at": "2099-01-01T00:00:00+00:00",
                "product_count": 1,
            }
        )
    )
    tool = SearchProductsTool(
        workspace=".", catalog=LocalSnapshotCatalogClient(tmp_path)
    )
    payload = json.loads(
        await tool.execute_with_context(CTX, q="", category="religiosas")
    )
    assert payload["category"]["confidence"] == "no_categories"
    assert [r["handle"] for r in payload["results"]] == ["corona"]
    # No debe pedirle al agente que ofrezca una lista vacía de categorías.
    assert "available" not in payload["category"]
