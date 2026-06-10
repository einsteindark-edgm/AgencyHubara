"""``EmitOrderStageWorkflow`` — emisión DURABLE de cambios de stage (L-8b).

El handler HTTP de orders lo arranca (fire-and-forget durable) en vez del
``asyncio.create_task`` best-effort. Una sola activity con retry policy:
la resolución de sesión pega a Medusa@Railway (lento y variable — L-2),
así que Temporal reintenta con backoff donde antes la task moría muda.

DEHA: workflow = adapter fino; toda la I/O vive en la activity.
"""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from src.plugins.orders.agent.activities.emit_stage import (
        emit_order_stage_activity,
    )


@workflow.defn(name="EmitOrderStageWorkflow")
class EmitOrderStageWorkflow:
    """Emite OrderStageChangedEvent para (order_id, to_stage), con retries."""

    @workflow.run
    async def run(self, input: dict) -> str:
        return await workflow.execute_activity(
            emit_order_stage_activity,
            args=[str(input.get("order_id", "")), str(input.get("to_stage", ""))],
            # Railway puede tardar 30s+ por GET (L-2); margen amplio + retries.
            start_to_close_timeout=timedelta(seconds=180),
            retry_policy=RetryPolicy(
                maximum_attempts=5,
                initial_interval=timedelta(seconds=5),
                backoff_coefficient=2.0,
            ),
        )
