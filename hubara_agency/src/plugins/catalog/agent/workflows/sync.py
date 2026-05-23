"""CatalogSyncWorkflow — disparado ON-DEMAND por un caller externo.

Three-step: pull desde Medusa → write atomico del snapshot → push a Meta
Commerce Catalog. Sin signals, sin queries, sin continue-as-new. El
snapshot ES el state durable; Meta es una proyección eventualmente
consistente del snapshot.

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
        from src.plugins.catalog.agent.workflows import CatalogSyncWorkflow
        from src.plugins.catalog.agent.contracts import CatalogSyncInput
        from src.platform.catalog.paths import get_snapshot_dir
        from src.platform.plugin_manifest import get_task_queue

        client = await get_temporal_client()
        handle = await client.start_workflow(
            CatalogSyncWorkflow.run,
            CatalogSyncInput(
                tenant_id="default",
                force_full_refresh=True,
                snapshot_dir=str(get_snapshot_dir()),
            ),
            id=f"catalog-sync-on-demand-{cause}-{int(time.time())}",
            task_queue=get_task_queue("catalog", "sync"),
        )
        result = await handle.result()  # opcional: esperar version

  - Ver `scripts/trigger_catalog_sync.py` para el patron completo.

────────────────────────────────────────────────────────────────────────
META PUSH — semántica del paso 3
────────────────────────────────────────────────────────────────────────
El paso `push_meta_catalog_activity` lee las credenciales Meta de env
(`META_CATALOG_ID` + `META_SYSTEM_USER_TOKEN`). Si alguna falta, hace
**graceful skip**: `push.pushed=False, push.ok=True`. El sync no falla —
el snapshot local sigue válido y los agentes (sales, remarketing) leen
de ahí. Esto permite tener Meta apagado en dev local sin romper el
workflow.

R-JSON: el token NUNCA entra al input del workflow (Temporal lo
persistiría en el event history). La activity lo lee de env, R-DET lo
permite.

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
activity caller — o la activity como fallback si llega vacio). Las
credenciales Meta las lee la activity push, no el workflow.
"""
from __future__ import annotations

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from src.plugins.catalog.agent.activities import (
        pull_medusa_catalog_activity,
        push_meta_catalog_activity,
        write_snapshot_activity,
    )
    from src.plugins.catalog.agent.contracts import (
        CatalogSyncInput,
        CatalogSyncResult,
        PushMetaActivityInput,
        WriteSnapshotInput,
    )
    from src.platform.temporal.retry_policies import (
        _CONV_OPTIONS,
        _TOOL_OPTIONS,
    )


@workflow.defn(name="CatalogSyncWorkflow")
class CatalogSyncWorkflow:
    @workflow.run
    async def run(self, input: CatalogSyncInput) -> CatalogSyncResult:
        # 1) Pull (activity con heartbeat).
        pull_result = await workflow.execute_activity(
            pull_medusa_catalog_activity,
            input,
            **_TOOL_OPTIONS,
        )

        # 2) Write atomico del snapshot.
        write_input = WriteSnapshotInput(
            products_json=pull_result.products_json,
            count=pull_result.count,
            fetched_at=pull_result.fetched_at,
            snapshot_dir=input.snapshot_dir,  # caller-provided o "" → activity fallback
            source_etag=pull_result.source_etag,
        )
        write_result = await workflow.execute_activity(
            write_snapshot_activity,
            write_input,
            **_CONV_OPTIONS,
        )

        # 3) Push a Meta Commerce Catalog. Graceful skip si env no configurado.
        # Reusamos `products_json` de pull (no re-leemos snapshot — ya está en
        # memoria del workflow). La activity lee/escribe `.meta_state.json`
        # en `snapshot_dir` para hashes incrementales y last_meta_count.
        # Usamos el `snapshot_dir` ya resuelto en write_input para que coincida
        # con el del snapshot escrito (no el `input.snapshot_dir` que podría
        # estar vacío y haber sido resuelto por fallback en write_snapshot).
        push_input = PushMetaActivityInput(
            tenant_id=input.tenant_id,
            products_json=pull_result.products_json,
            snapshot_dir=write_input.snapshot_dir,
        )
        push_result = await workflow.execute_activity(
            push_meta_catalog_activity,
            push_input,
            **_TOOL_OPTIONS,
        )

        return CatalogSyncResult(write=write_result, push=push_result)
