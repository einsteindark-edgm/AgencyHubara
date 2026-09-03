"""CatalogSyncWorkflow — WorkflowEnvironment con activities fakes.

El workflow ahora hace 3 pasos:
  1. pull_medusa_catalog (fake_pull)
  2. write_snapshot (fake_write)
  3. push_meta_catalog (fake_push) — graceful skip si META env no seteado

Los fakes simulan el contrato sin tocar HTTP / filesystem real.
"""
from __future__ import annotations

import json

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.catalog.agent.contracts import (
    CatalogSyncInput,
    PullCatalogResult,
    PushMetaActivityInput,
    PushMetaActivityResult,
    WriteSnapshotInput,
    WriteSnapshotResult,
)
from src.plugins.catalog.agent.workflows import CatalogSyncWorkflow


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


@activity.defn(name="push_meta_catalog")
async def fake_push(input: PushMetaActivityInput) -> PushMetaActivityResult:
    # Validamos que el workflow esta pasando el producto_json y el
    # snapshot_dir correcto (no env, no Medusa re-fetch).
    assert "h" in input.products_json
    assert input.snapshot_dir == "/tmp/test-snapshot"
    assert input.tenant_id == "default"
    return PushMetaActivityResult(
        ok=True,
        pushed=True,
        handle="batch_xyz",
        creates=1,
        updates=0,
        deletes=0,
        skipped_image=0,
        skipped_price=0,
        aborted_due_to_threshold=False,
        error=None,
        duration_seconds=0.5,
    )


@activity.defn(name="push_meta_catalog")
async def fake_push_skipped(
    input: PushMetaActivityInput,
) -> PushMetaActivityResult:
    """Simula el graceful skip cuando Meta no esta configurado."""
    return PushMetaActivityResult(
        ok=True,
        pushed=False,
        handle=None,
        creates=0,
        updates=0,
        deletes=0,
        skipped_image=0,
        skipped_price=0,
        aborted_due_to_threshold=False,
        error="meta_not_configured",
        duration_seconds=0.0,
    )


@pytest.mark.asyncio
async def test_workflow_runs_pull_write_push():
    """Happy path completo: los 3 pasos se ejecutan y el result agrega
    write + push."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-catalog-sync",
            workflows=[CatalogSyncWorkflow],
            activities=[fake_pull, fake_write, fake_push],
        ):
            result = await env.client.execute_workflow(
                CatalogSyncWorkflow.run,
                CatalogSyncInput(snapshot_dir="/tmp/test-snapshot"),
                id="test-1",
                task_queue="test-catalog-sync",
            )
            # Write step
            assert result.write.version == "abc"
            assert result.write.files_written == 3
            # Push step
            assert result.push.ok is True
            assert result.push.pushed is True
            assert result.push.handle == "batch_xyz"
            assert result.push.creates == 1


@pytest.mark.asyncio
async def test_workflow_continues_when_meta_not_configured():
    """Si el push hace graceful skip, el sync sigue siendo OK (snapshot
    valido). El workflow no falla — el caller puede operar con local
    snapshot incluso sin Meta."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-catalog-sync-skip",
            workflows=[CatalogSyncWorkflow],
            activities=[fake_pull, fake_write, fake_push_skipped],
        ):
            result = await env.client.execute_workflow(
                CatalogSyncWorkflow.run,
                CatalogSyncInput(snapshot_dir="/tmp/test-snapshot"),
                id="test-skip-1",
                task_queue="test-catalog-sync-skip",
            )
            assert result.write.version == "abc"  # snapshot OK
            assert result.push.pushed is False
            assert result.push.error == "meta_not_configured"
            assert result.push.ok is True  # NOT a failure


@pytest.mark.asyncio
async def test_progress_query_reports_live_steps():
    """La query `progress` refleja el step-by-step real: tras completar, los 3
    pasos quedan en su estado final (done) con sus detalles. Es exactamente lo
    que pollea el dashboard (`GET /api/catalog/sync/{id}`) para pintar el
    progreso en vivo."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-catalog-progress",
            workflows=[CatalogSyncWorkflow],
            activities=[fake_pull, fake_write, fake_push],
        ):
            handle = await env.client.start_workflow(
                CatalogSyncWorkflow.run,
                CatalogSyncInput(snapshot_dir="/tmp/test-snapshot"),
                id="test-progress-1",
                task_queue="test-catalog-progress",
            )
            await handle.result()
            progress = json.loads(await handle.query("progress"))

    assert progress["phase"] == "completed"
    assert progress["product_count"] == 1
    assert progress["version"] == "abc"
    assert [s["key"] for s in progress["steps"]] == ["pull", "write", "push"]
    by_key = {s["key"]: s for s in progress["steps"]}
    assert by_key["pull"]["status"] == "done"
    assert by_key["pull"]["detail"] == "1 productos"
    assert by_key["write"]["status"] == "done"
    assert by_key["push"]["status"] == "done"


@pytest.mark.asyncio
async def test_progress_query_marks_push_skipped_when_meta_off():
    """Si Meta no esta configurado, el paso push queda `skipped` (no `failed`)
    — el dashboard lo pinta como 'omitido', no como error."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-catalog-progress-skip",
            workflows=[CatalogSyncWorkflow],
            activities=[fake_pull, fake_write, fake_push_skipped],
        ):
            handle = await env.client.start_workflow(
                CatalogSyncWorkflow.run,
                CatalogSyncInput(snapshot_dir="/tmp/test-snapshot"),
                id="test-progress-skip-1",
                task_queue="test-catalog-progress-skip",
            )
            await handle.result()
            progress = json.loads(await handle.query("progress"))

    by_key = {s["key"]: s for s in progress["steps"]}
    assert by_key["push"]["status"] == "skipped"
    assert "omitido" in by_key["push"]["detail"]


@activity.defn(name="push_meta_catalog")
async def fake_push_replica_failed(
    input: PushMetaActivityInput,
) -> PushMetaActivityResult:
    """Primario OK, réplica 5xx: la activity devuelve ok=False sin raise."""
    return PushMetaActivityResult(
        ok=False,
        pushed=True,
        handle="batch_primary",
        creates=1,
        updates=0,
        deletes=0,
        skipped_image=0,
        skipped_price=0,
        aborted_due_to_threshold=False,
        error="CAT-REPLICA-002: http_500: boom",
        duration_seconds=0.5,
        catalogs_pushed=2,
    )


@pytest.mark.asyncio
async def test_push_failure_surfaces_in_progress_error_and_step(
    ):
    """PM-04: si el push devuelve ok=False (ej. una réplica falló), el
    workflow completa (el snapshot local sigue válido) pero `progress.error`
    trae el error y el step push queda `failed` con el detalle — así la
    historia de syncs no lo pinta verde."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-catalog-push-failed",
            workflows=[CatalogSyncWorkflow],
            activities=[fake_pull, fake_write, fake_push_replica_failed],
        ):
            handle = await env.client.start_workflow(
                CatalogSyncWorkflow.run,
                CatalogSyncInput(snapshot_dir="/tmp/test-snapshot"),
                id="test-push-failed-1",
                task_queue="test-catalog-push-failed",
            )
            result = await handle.result()
            progress = json.loads(await handle.query("progress"))

    assert result.push.ok is False
    assert progress["phase"] == "completed"
    assert progress["error"] == "CAT-REPLICA-002: http_500: boom"
    by_key = {s["key"]: s for s in progress["steps"]}
    assert by_key["push"]["status"] == "failed"
    assert "2 catálogos" in by_key["push"]["detail"]
    assert "CAT-REPLICA-002" in by_key["push"]["detail"]
