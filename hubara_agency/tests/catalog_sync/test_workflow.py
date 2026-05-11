"""CatalogSyncWorkflow — WorkflowEnvironment con activities fakes."""
from __future__ import annotations

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.catalog_sync.contracts import (
    CatalogSyncInput,
    PullCatalogResult,
    WriteSnapshotInput,
    WriteSnapshotResult,
)
from src.catalog_sync.workflows import CatalogSyncWorkflow


@activity.defn(name="pull_medusa_catalog")
async def fake_pull(input: CatalogSyncInput) -> PullCatalogResult:
    return PullCatalogResult(
        products_json='[{"id":"1","handle":"h","title":"T","status":"published"}]',
        count=1,
        fetched_at="2026-05-07T12:00:00+00:00",
    )


@activity.defn(name="write_snapshot")
async def fake_write(input: WriteSnapshotInput) -> WriteSnapshotResult:
    assert input.count == 1
    assert "h" in input.products_json
    assert input.snapshot_dir == "/tmp/test-snapshot"
    return WriteSnapshotResult(
        version="abc", bytes_written=100, files_written=3
    )


@pytest.mark.asyncio
async def test_workflow_calls_pull_then_write():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-catalog-sync",
            workflows=[CatalogSyncWorkflow],
            activities=[fake_pull, fake_write],
        ):
            result = await env.client.execute_workflow(
                CatalogSyncWorkflow.run,
                CatalogSyncInput(snapshot_dir="/tmp/test-snapshot"),
                id="test-1",
                task_queue="test-catalog-sync",
            )
            assert result.version == "abc"
            assert result.files_written == 3
