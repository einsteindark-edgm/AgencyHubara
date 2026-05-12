"""CatalogSyncWorkflow — disparado ON-DEMAND por un caller externo.

Single-shot: pull desde Medusa → write atomico del snapshot. Sin signals,
sin queries, sin continue-as-new. El snapshot ES el state durable.

────────────────────────────────────────────────────────────────────────
CÓMO SE DISPARA (v1)
────────────────────────────────────────────────────────────────────────
NO hay Temporal Schedule periódico. El workflow se dispara on-demand
después de cualquier mutación del catálogo en Medusa (create/update/
delete de producto). Quien debería disparar:

  - Ops manualmente:
      uv run python scripts/trigger_catalog_sync.py

  - El futuro `product_sync_agent` (cuando exista): cada vez que termine
    un workflow de update de productos, debe ejecutar (dentro de una
    `@activity.defn` — el cliente Temporal NO puede vivir en un workflow
    por R-DIP):

        from src.platform.temporal.client import get_temporal_client
        from src.catalog_sync.workflows import CatalogSyncWorkflow
        from src.catalog_sync.contracts import CatalogSyncInput
        from src.platform.catalog.paths import get_snapshot_dir
        from src.platform.constants import CATALOG_SYNC_QUEUE

        client = await get_temporal_client()
        handle = await client.start_workflow(
            CatalogSyncWorkflow.run,
            CatalogSyncInput(
                tenant_id="default",
                force_full_refresh=True,
                snapshot_dir=str(get_snapshot_dir()),
            ),
            id=f"catalog-sync-on-demand-{cause}-{int(time.time())}",
            task_queue=CATALOG_SYNC_QUEUE,
        )
        result = await handle.result()  # opcional: esperar version

  - Ver `scripts/trigger_catalog_sync.py` para el patron completo.

────────────────────────────────────────────────────────────────────────
RAZÓN: por qué NO usamos Schedule periódico
────────────────────────────────────────────────────────────────────────
Una Schedule cada N min introduce ventanas de staleness (entre que
algo cambia en Medusa y el siguiente tick). Trigger-by-event reduce
esa ventana a segundos y elimina sync innecesario cuando no hay
cambios. Si en el futuro hace falta detectar cambios externos a
nuestra API (ej. Medusa Admin Web), añadir un Schedule complementario
es trivial — el workflow no cambia.

────────────────────────────────────────────────────────────────────────
R-DET: cero `time.time()`, cero `datetime.now()`, cero `os.environ`. El
`snapshot_dir` cruza por input (lo resuelve el caller — script o
activity caller — o la activity como fallback si llega vacio).
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
