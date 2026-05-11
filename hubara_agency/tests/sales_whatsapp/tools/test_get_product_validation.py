"""GetProductByHandleTool — schema constraints."""
from __future__ import annotations

from pathlib import Path

from src.sales_whatsapp.tools.catalog import GetProductByHandleTool


class _NeverCalled:
    async def search(self, *a, **k): raise NotImplementedError
    async def get_by_handle(self, *a, **k): raise NotImplementedError


def test_handle_schema(tmp_path: Path):
    tool = GetProductByHandleTool(
        workspace=tmp_path, catalog=_NeverCalled()
    )
    assert tool.parameters["properties"]["handle"]["minLength"] == 1
    assert tool.parameters["properties"]["handle"]["maxLength"] == 200
    assert tool.parameters["required"] == ["handle"]
    assert tool.name == "get_product_by_handle"
