"""GetProductByHandleTool — handle inexistente → found:false. Anti-alucinacion."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog.errors import ProductNotFoundError
from src.sales_whatsapp.tools.catalog import GetProductByHandleTool


class _NotFoundCatalog:
    async def search(self, *a, **k):
        raise NotImplementedError

    async def get_by_handle(self, handle):
        raise ProductNotFoundError(handle)


@pytest.mark.asyncio
async def test_get_not_found_returns_envelope(tmp_path: Path):
    tool = GetProductByHandleTool(
        workspace=tmp_path, catalog=_NotFoundCatalog()
    )
    out = await tool.execute_with_context(
        ToolContext(session_key="s", channel="whatsapp", chat_id="c"),
        handle="inventado",
    )
    payload = json.loads(out)
    assert payload["found"] is False
    assert "inventado" in payload["message"]
    assert "search_products" in payload["message"]  # nudge al LLM
