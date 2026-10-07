"""SearchProductsTool — `stale` y `manifest` NO llegan al LLM.

Incidente 2026-10-06: el envelope decía `stale: true` (la copia local del
catálogo tenía más de 30 minutos) y TOOLS.md tenía que gastar una regla en
pedirle al LLM que lo ignorara. La copia se refresca A MANO (botón Sync del
dashboard, decisión del operador): su edad es asunto del operador, que la ve
en el dashboard y en el log, no del bot, para el que la copia es la verdad.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext
from loguru import logger

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
async def test_the_llm_does_not_read_how_old_the_copy_is(tmp_path: Path):
    tool = SearchProductsTool(workspace=tmp_path, catalog=_StaleCatalog())
    out = await tool.execute_with_context(
        ToolContext(session_key="s", channel="whatsapp", chat_id="c"),
        q="x",
    )
    payload = json.loads(out)
    assert "stale" not in payload
    assert "manifest" not in payload
    assert payload["count"] == 0


@pytest.mark.asyncio
async def test_the_log_still_says_the_copy_is_old(tmp_path: Path):
    lines: list[str] = []
    sink = logger.add(lines.append, format="{message}")
    try:
        tool = SearchProductsTool(workspace=tmp_path, catalog=_StaleCatalog())
        await tool.execute_with_context(
            ToolContext(session_key="s", channel="whatsapp", chat_id="c"),
            q="x",
        )
    finally:
        logger.remove(sink)
    assert any("stale=True" in line for line in lines)
