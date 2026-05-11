"""CatalogSyncWorkflow — disparado por Temporal Schedule cada N min.

Single-shot: pull desde Medusa → write atomico. Sin signals, sin queries,
sin continue-as-new. El snapshot ES el state durable.

R-DET: cero `time.time()`, cero `datetime.now()`, cero `os.environ`. El
`snapshot_dir` cruza por input (lo resuelve el caller — script Schedule
o smoke runner — o la activity como fallback).
"""
from __future__ import annotations

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from src.catalog_sync.activities import (
        pull_medusa_catalog_activity,
        write_snapshot_activity,
    )
    from src.catalog_sync.contracts import (
        CatalogSyncInput,
        WriteSnapshotInput,
        WriteSnapshotResult,
    )
    from src.platform.temporal.retry_policies import (
        _CONV_OPTIONS,
        _TOOL_OPTIONS,
    )


@workflow.defn(name="CatalogSyncWorkflow")
class CatalogSyncWorkflow:
    @workflow.run
    async def run(self, input: CatalogSyncInput) -> WriteSnapshotResult:
        # 1) Pull (activity con heartbeat).
        pull_result = await workflow.execute_activity(
            pull_medusa_catalog_activity,
            input,
            **_TOOL_OPTIONS,
        )

        # 2) Write atomico.
        write_input = WriteSnapshotInput(
            products_json=pull_result.products_json,
            count=pull_result.count,
            fetched_at=pull_result.fetched_at,
            snapshot_dir=input.snapshot_dir,  # caller-provided o "" → activity fallback
            source_etag=pull_result.source_etag,
        )
        return await workflow.execute_activity(
            write_snapshot_activity,
            write_input,
            **_CONV_OPTIONS,
        )
