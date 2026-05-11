"""GetProductByHandleTool — handle existente devuelve envelope con product."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog.dtos import (
    CatalogPriceDTO,
    CatalogProductDTO,
    CatalogVariantDTO,
)
from src.sales_whatsapp.tools.catalog import GetProductByHandleTool


class _FakeCatalog:
    async def search(self, *a, **k):
        raise NotImplementedError

    async def get_by_handle(self, handle):
        return CatalogProductDTO(
            id="1",
            handle=handle,
            title="Luz Serena",
            status="published",
            description="Una vela serena.",
            variants=[
                CatalogVariantDTO(
                    id="v1",
                    title="u",
                    prices=[
                        CatalogPriceDTO(amount="23000", currency_code="cop")
                    ],
                )
            ],
        )


@pytest.mark.asyncio
async def test_get_found(tmp_path: Path):
    tool = GetProductByHandleTool(workspace=tmp_path, catalog=_FakeCatalog())
    out = await tool.execute_with_context(
        ToolContext(session_key="s", channel="whatsapp", chat_id="c"),
        handle="luz-serena",
    )
    payload = json.loads(out)
    assert payload["found"] is True
    assert payload["product"]["handle"] == "luz-serena"
    assert payload["product"]["title"] == "Luz Serena"
    assert payload["product"]["description"] == "Una vela serena."
    assert payload["product"]["variants"][0]["price"] == "23000"
    assert payload["product"]["variants"][0]["currency"] == "cop"
