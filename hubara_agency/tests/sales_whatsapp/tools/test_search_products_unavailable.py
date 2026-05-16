"""SearchProductsTool — falla del catalogo → envelope, NO crashea."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog.errors import CatalogUnavailableError
from src.plugins.chats.agent.sales.tools.catalog import SearchProductsTool


class _BrokenCatalog:
    async def search(self, *a, **k):
        raise CatalogUnavailableError(
            "snapshot not found at /tmp/x/snapshot.json"
        )

    async def get_by_handle(self, *a, **k):
        raise NotImplementedError


@pytest.mark.asyncio
async def test_unavailable_returns_error_envelope(tmp_path: Path):
    tool = SearchProductsTool(workspace=tmp_path, catalog=_BrokenCatalog())
    out = await tool.execute_with_context(
        ToolContext(session_key="s", channel="whatsapp", chat_id="c"),
        q="x",
    )
    payload = json.loads(out)
    assert payload["error"] == "catalog_unavailable"
    assert "no está disponible" in payload["message"]
    assert "detail" in payload
