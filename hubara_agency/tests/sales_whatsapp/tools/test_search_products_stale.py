"""SearchProductsTool — stale flag propaga al envelope."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog.dtos import CatalogManifestDTO, SearchResult
from src.plugins.chats.agent.sales.tools.catalog import SearchProductsTool


class _StaleCatalog:
    async def search(self, q, *, limit=10):
        return SearchResult(
            query=q,
            count=0,
            truncated=False,
            stale=True,
            manifest=CatalogManifestDTO(
                version="old",
                fetched_at="2020-01-01T00:00:00+00:00",
                product_count=5,
            ),
            results=[],
        )

    async def get_by_handle(self, h):
        raise NotImplementedError


@pytest.mark.asyncio
async def test_stale_flag_propagates(tmp_path: Path):
    tool = SearchProductsTool(workspace=tmp_path, catalog=_StaleCatalog())
    out = await tool.execute_with_context(
        ToolContext(session_key="s", channel="whatsapp", chat_id="c"),
        q="x",
    )
    payload = json.loads(out)
    assert payload["stale"] is True
