"""SearchProductsTool — envelope shape conforme al closed-list contract."""
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
from src.sales_whatsapp.tools.catalog import SearchProductsTool


class _FakeCatalog:
    async def search(self, q, *, limit=10):
        return SearchResult(
            query=q,
            count=1,
            truncated=False,
            stale=False,
            manifest=CatalogManifestDTO(
                version="v1",
                fetched_at="2099-01-01T00:00:00+00:00",
                product_count=1,
            ),
            results=[
                CatalogProductDTO(
                    id="1",
                    handle="vela-aroma-lavanda",
                    title="Vela Lavanda",
                    status="published",
                    thumbnail="https://r2.example.com/lavanda.jpg",
                    variants=[
                        CatalogVariantDTO(
                            id="v1",
                            title="u",
                            prices=[
                                CatalogPriceDTO(
                                    amount="49000", currency_code="cop"
                                )
                            ],
                        )
                    ],
                    tags=["Aroma: Lavanda"],
                )
            ],
        )

    async def get_by_handle(self, h):
        raise NotImplementedError


@pytest.mark.asyncio
async def test_envelope_shape(tmp_path: Path):
    tool = SearchProductsTool(workspace=tmp_path, catalog=_FakeCatalog())
    out = await tool.execute_with_context(
        ToolContext(session_key="s", channel="whatsapp", chat_id="c"),
        q="lavanda",
        limit=10,
    )
    payload = json.loads(out)
    assert payload["query"] == "lavanda"
    assert payload["count"] == 1
    assert payload["stale"] is False
    assert payload["truncated"] is False
    assert "manifest" in payload
    assert payload["manifest"]["version"] == "v1"
    r = payload["results"][0]
    for k in (
        "id",
        "handle",
        "title",
        "price",
        "currency",
        "in_stock",
        "thumbnail_url",
        "tags",
    ):
        assert k in r
    assert r["handle"] == "vela-aroma-lavanda"
    assert r["price"] == "49000"
    assert r["currency"] == "cop"
    assert r["thumbnail_url"] == "https://r2.example.com/lavanda.jpg"
    assert r["tags"] == ["Aroma: Lavanda"]
