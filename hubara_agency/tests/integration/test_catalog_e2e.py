"""Integration test — escribe snapshot manual + Sales tool lo consume.

NO requiere Medusa real ni Temporal. Verifica que el contrato entre los
DTOs de HU-02, el escritor de HU-03 (use case directo) y las tools de
HU-04 esten alineados end-to-end.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.plugins.catalog.agent.contracts import WriteSnapshotInput
from src.plugins.catalog.agent.use_cases.write_snapshot import WriteSnapshotUseCase
from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient
from src.plugins.chats.agent.sales.tools.catalog import (
    GetProductByHandleTool,
    SearchProductsTool,
)


@pytest.mark.asyncio
async def test_e2e_write_then_read_then_tool(tmp_path: Path):
    # 1) Sync agent escribe snapshot (HU-03 use case puro).
    products = [
        {
            "id": "1",
            "handle": "luz-serena",
            "title": "Luz Serena",
            "status": "published",
            "variants": [
                {
                    "id": "v1",
                    "title": "u",
                    "prices": [
                        {"amount": "23000", "currency_code": "cop"}
                    ],
                }
            ],
        },
        {
            "id": "2",
            "handle": "vela-cruz",
            "title": "Cruz de Vida",
            "status": "published",
            "variants": [
                {
                    "id": "v2",
                    "title": "u",
                    "prices": [
                        {"amount": "17000", "currency_code": "cop"}
                    ],
                }
            ],
        },
    ]
    use_case = WriteSnapshotUseCase()
    await use_case.execute(
        WriteSnapshotInput(
            products_json=json.dumps(products),
            count=2,
            fetched_at="2099-05-07T12:00:00+00:00",  # future-dated → no stale
            snapshot_dir=str(tmp_path),
        )
    )

    # 2) HU-02 reader.
    catalog = LocalSnapshotCatalogClient(tmp_path)

    # 3) HU-04 search tool consume.
    search_tool = SearchProductsTool(workspace=tmp_path, catalog=catalog)
    out = await search_tool.execute_with_context(
        ToolContext(session_key="s", channel="whatsapp", chat_id="c"),
        q="luz",
    )
    payload = json.loads(out)
    assert payload["count"] == 1
    assert payload["stale"] is False
    assert payload["results"][0]["handle"] == "luz-serena"
    assert payload["results"][0]["price"] == "23000"

    # 4) get_product_by_handle tambien funciona.
    get_tool = GetProductByHandleTool(workspace=tmp_path, catalog=catalog)
    out = await get_tool.execute_with_context(
        ToolContext(session_key="s", channel="whatsapp", chat_id="c"),
        handle="vela-cruz",
    )
    payload = json.loads(out)
    assert payload["found"] is True
    assert payload["product"]["title"] == "Cruz de Vida"

    # 5) Handle inexistente → found:false (closed-list anti-alucinacion).
    out = await get_tool.execute_with_context(
        ToolContext(session_key="s", channel="whatsapp", chat_id="c"),
        handle="patata-frita",
    )
    payload = json.loads(out)
    assert payload["found"] is False
    assert "search_products" in payload["message"]
