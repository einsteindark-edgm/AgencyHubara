"""pull_medusa_catalog_activity — ActivityEnvironment + Fake use case."""
from __future__ import annotations

import pytest
from temporalio.testing import ActivityEnvironment

from src.catalog_sync.activities.pull import pull_medusa_catalog_activity
from src.catalog_sync.contracts import CatalogSyncInput, PullCatalogResult


class _FakeUseCase:
    async def execute(self, input):
        return PullCatalogResult(
            products_json="[]",
            count=0,
            fetched_at="2026-01-01T00:00:00Z",
        )


@pytest.mark.asyncio
async def test_activity_calls_use_case(monkeypatch):
    monkeypatch.setattr(
        "src.catalog_sync.activities.pull.get_pull_catalog_use_case",
        lambda: _FakeUseCase(),
    )

    env = ActivityEnvironment()
    result = await env.run(
        pull_medusa_catalog_activity, CatalogSyncInput()
    )
    assert result.count == 0
    assert result.products_json == "[]"
