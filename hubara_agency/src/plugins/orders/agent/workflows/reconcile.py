"""OrderReconciliationWorkflow — disparado por un Temporal Schedule periódico.

Un paso: ejecuta la activity de reconciliación. El Schedule (creado por el
worker al boot, ver `workers/reconcile.py`) lo dispara cada N minutos con
`overlap=SKIP` (nunca dos barridos solapados). La idempotencia real vive en
`reconcile_one` (platform/orders/reconciliation.py): un tick que cruza con un
draft ya creado NO duplica.

R-DET: cero I/O, cero env, cero now()/random en `@workflow.run`. El input
cruza por valor; la activity resuelve el vault_dir desde env.
"""
from __future__ import annotations

import dataclasses

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from src.platform.temporal.retry_policies import _TOOL_OPTIONS
    from src.plugins.orders.agent.activities import (
        reconcile_pending_orders_activity,
        sync_order_totals_activity,
    )
    from src.plugins.orders.agent.contracts import ReconcileInput, ReconcileResult


@workflow.defn(name="OrderReconciliationWorkflow")
class OrderReconciliationWorkflow:
    @workflow.run
    async def run(self, input: ReconcileInput) -> ReconcileResult:
        result = await workflow.execute_activity(
            reconcile_pending_orders_activity,
            input,
            **_TOOL_OPTIONS,  # type: ignore[arg-type]
        )
        # Pedido #31: alinear totales editados en Medusa con el vault (Ads).
        # `patched`: runs en vuelo durante el deploy replayan sin este paso.
        if not workflow.patched("sync-order-totals"):
            return result
        updated = await workflow.execute_activity(
            sync_order_totals_activity,
            input,
            **_TOOL_OPTIONS,  # type: ignore[arg-type]
        )
        return dataclasses.replace(result, totals_updated=updated)
