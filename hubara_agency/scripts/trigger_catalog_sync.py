"""Dispara UN catalog sync on-demand (NO Schedule periódico).

Camino canónico para v1: el agente que sube/actualiza productos en
Medusa (futuro `product_sync_agent`) llama este workflow on-demand
después de cada operación de update — NO hay Schedule periódico.

────────────────────────────────────────────────────────────────────
USO DESDE OPS (manual)
────────────────────────────────────────────────────────────────────
    # Dispara y espera resultado (recomendado para verificación):
    uv run python scripts/trigger_catalog_sync.py

    # Fire-and-forget (no espera el resultado):
    uv run python scripts/trigger_catalog_sync.py --no-wait

────────────────────────────────────────────────────────────────────
USO DESDE OTRO AGENTE / SERVICIO (programmatic)
────────────────────────────────────────────────────────────────────
El futuro `product_sync_agent` (o cualquier otro caller que actualice
Medusa) debe disparar el sync así, dentro de una `@activity.defn` de
ese agente (porque la activity es el lugar que puede usar
`temporal_client` — los workflows NO):

    from temporalio import activity

    from src.plugins.catalog.agent.contracts import CatalogSyncInput
    from src.plugins.catalog.agent.workflows import CatalogSyncWorkflow
    from src.platform.catalog.paths import get_snapshot_dir
    from src.platform.plugin_manifest import get_task_queue
    from src.platform.temporal.client import get_temporal_client


    @activity.defn(name="trigger_catalog_resync")
    async def trigger_catalog_resync_activity(triggered_by: str) -> str:
        '''
        Ejecutado por `product_sync_agent` después de cualquier
        operación que cambie el catálogo en Medusa (create/update/delete
        de producto).
        '''
        client = await get_temporal_client()
        handle = await client.start_workflow(
            CatalogSyncWorkflow.run,
            CatalogSyncInput(
                tenant_id="default",
                force_full_refresh=True,
                snapshot_dir=str(get_snapshot_dir()),
            ),
            id=f"catalog-sync-on-demand-{triggered_by}-{int(time.time())}",
            task_queue=get_task_queue("catalog", "sync"),
        )
        # Opcional: esperar el resultado para confirmar que el snapshot
        # se actualizo antes de retornar. Si la activity es invocada en
        # cascada (ej. al final de un workflow de update), se puede
        # omitir el await result y dejar fire-and-forget.
        result = await handle.result()
        return result.version


Reglas DEHA importantes a respetar cuando se construya el caller:
  - El `temporal_client` SOLO puede vivir en una activity, NUNCA en
    un workflow (R-DIP). Por eso el snippet de arriba es una activity.
  - Si el caller workflow quiere fire-and-forget, ejecuta esta activity
    con `start_to_close_timeout=timedelta(seconds=30)` y sin await
    del result, o con retry policy permisiva (el sync mismo tiene su
    propio retry interno).

────────────────────────────────────────────────────────────────────
POR QUÉ NO HAY SCHEDULE PERIÓDICO (decisión v1)
────────────────────────────────────────────────────────────────────
El snapshot debe estar SIEMPRE en sync con Medusa. Una Schedule cada N
minutos genera ventanas de staleness (entre que cambia algo en Medusa
y el siguiente tick). El patrón correcto es trigger-by-event: el
agente que MUTA el catálogo dispara el resync inmediatamente después
de mutar. Esto:

  1. Reduce staleness ventana a ~segundos (tiempo del workflow) en vez
     de minutos.
  2. Elimina sync wasteful cuando no hay cambios.
  3. Mantiene observabilidad del trigger (audit trail: por qué se
     ejecutó cada sync, vs "porque sí, era la hora").

Si en algún momento se reintroduce una Schedule (ej. para detectar
cambios externos a Medusa Admin API), se puede recuperar el código
viejo desde `git log -- scripts/create_catalog_sync_schedule.py`.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

# Permitir ejecucion directa (`python scripts/...py`).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from temporalio.client import Client  # noqa: E402

from src.plugins.catalog.agent.contracts import CatalogSyncInput  # noqa: E402
from src.plugins.catalog.agent.workflows import CatalogSyncWorkflow  # noqa: E402
from src.platform.catalog.paths import get_snapshot_dir  # noqa: E402
from src.platform.plugin_manifest import get_task_queue  # noqa: E402
from src.platform.temporal.client import get_temporal_client  # noqa: E402


async def trigger_sync(*, wait_for_result: bool, triggered_by: str) -> None:
    snapshot_dir = str(get_snapshot_dir())
    workflow_id = f"catalog-sync-on-demand-{triggered_by}-{int(time.time())}"

    client: Client = await get_temporal_client()
    handle = await client.start_workflow(
        CatalogSyncWorkflow.run,
        CatalogSyncInput(
            tenant_id="default",
            force_full_refresh=True,
            snapshot_dir=snapshot_dir,
        ),
        id=workflow_id,
        task_queue=get_task_queue("catalog", "sync"),
    )
    print(f"Started workflow {workflow_id}")
    print(f"  snapshot_dir: {snapshot_dir}")

    if wait_for_result:
        result = await handle.result()
        print(
            f"\n✅ Sync completed\n"
            f"  version: {result.version}\n"
            f"  bytes_written: {result.bytes_written}\n"
            f"  files_written: {result.files_written}"
        )
    else:
        print("\n(fire-and-forget — workflow corre en background)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Fire-and-forget: no esperar al resultado del workflow.",
    )
    parser.add_argument(
        "--triggered-by",
        default="manual",
        help=(
            "Etiqueta de auditoria que se incluye en el workflow ID. "
            "El futuro product_sync_agent usaría algo como "
            "'product_updated' o 'product_created'."
        ),
    )
    args = parser.parse_args()
    try:
        asyncio.run(
            trigger_sync(
                wait_for_result=not args.no_wait,
                triggered_by=args.triggered_by,
            )
        )
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
