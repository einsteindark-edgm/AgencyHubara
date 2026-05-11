"""SearchProductsTool — Protocol compliance + JSON schema shape."""
from __future__ import annotations

from pathlib import Path

from src.sales_whatsapp.tools.catalog import SearchProductsTool


class _FakeCatalog:
    async def search(self, q, *, limit=10): raise NotImplementedError
    async def get_by_handle(self, handle): raise NotImplementedError


def test_protocol_compliance(tmp_path: Path):
    tool = SearchProductsTool(workspace=tmp_path, catalog=_FakeCatalog())
    assert tool.name == "search_products"
    assert "q" in tool.parameters["properties"]
    assert "limit" in tool.parameters["properties"]
    assert tool.parameters["required"] == ["q"]
    assert hasattr(tool, "execute_with_context")


def test_schema_constraints(tmp_path: Path):
    tool = SearchProductsTool(workspace=tmp_path, catalog=_FakeCatalog())
    assert tool.parameters["properties"]["q"]["minLength"] == 1
    assert tool.parameters["properties"]["q"]["maxLength"] == 100
    assert tool.parameters["properties"]["limit"]["minimum"] == 1
    assert tool.parameters["properties"]["limit"]["maximum"] == 20
    assert tool.parameters["properties"]["limit"]["default"] == 10
