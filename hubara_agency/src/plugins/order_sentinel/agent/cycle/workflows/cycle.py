"""OrderSentinelCycleWorkflow — UN ciclo del Order Sentinel.

snapshot (vault: tag HUMANO + orden + watermark) → dispatch a la caja
GraphAgents → poll hasta terminal → execute intents contra la API de orders
(notify_customer=false) + cierre de watermarks. La cadencia entre ciclos la
da un Temporal Schedule (1x/día — una sola prendida de la caja); el workflow
es one-shot (no loop eterno).

R-DET: cero I/O acá — todo side-effect es activity; el poll loop usa
`workflow.sleep` (durable). Run failed → NO se ejecutan intents ni se
escriben watermarks: el próximo ciclo re-analiza (idempotente — la
validación DAG del API vuelve no-op lo ya aplicado).
"""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from src.plugins.order_sentinel.agent.cycle.activities import (
        build_order_sentinel_snapshot_activity,
        dispatch_order_sentinel_activity,
        execute_order_intents_activity,
        poll_order_sentinel_activity,
    )

#: intervalo entre polls del run (la caja tarda segundos-minutos por ciclo).
_POLL_INTERVAL = timedelta(seconds=15)

#: tope de polls por ciclo (~15 min) — un run colgado NO cuelga el ciclo.
_MAX_POLLS = 60


@workflow.defn(name="OrderSentinelCycleWorkflow")
class OrderSentinelCycleWorkflow:
    @workflow.run
    async def run(self) -> dict:
        snapshot = await workflow.execute_activity(
            build_order_sentinel_snapshot_activity,
            start_to_close_timeout=timedelta(seconds=120),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        if not snapshot.get("conversations"):
            workflow.logger.info(
                "OrderSentinelCycle: snapshot vacío — no se paga cold start."
            )
            return {
                "applied": 0,
                "skipped": 0,
                "failed": 0,
                "run_status": "skipped_empty",
            }

        run_id = f"{workflow.info().workflow_id}:{workflow.info().run_id}"
        execution_id = await workflow.execute_activity(
            dispatch_order_sentinel_activity,
            args=[snapshot, run_id],
            # cold start EC2 (1-3 min) + dispatch SSM — generoso a propósito.
            start_to_close_timeout=timedelta(minutes=10),
            heartbeat_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )

        state: dict = {"status": "running"}
        for _ in range(_MAX_POLLS):
            state = await workflow.execute_activity(
                poll_order_sentinel_activity,
                args=[execution_id],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            if state.get("status") in ("completed", "failed"):
                break
            await workflow.sleep(_POLL_INTERVAL)

        if state.get("status") != "completed":
            workflow.logger.warning(
                "OrderSentinelCycle: run %s terminó en %s (%s) — sin execute "
                "ni watermarks (el próximo ciclo re-analiza).",
                execution_id,
                state.get("status"),
                state.get("error"),
            )
            return {
                "applied": 0,
                "skipped": 0,
                "failed": 0,
                "run_status": str(state.get("status")),
                "execution_id": execution_id,
            }

        result = state.get("result") or {}
        intents = result.get("dispatch") or []
        summary = await workflow.execute_activity(
            execute_order_intents_activity,
            args=[intents, snapshot.get("watermarks") or {}],
            start_to_close_timeout=timedelta(minutes=5),
            # Un retry re-corre TODOS los intents: lo ya aplicado cae en
            # invalid_transition → skipped (benigno, idempotente).
            retry_policy=RetryPolicy(maximum_attempts=2),
        )

        return {
            "applied": int(summary.get("applied", 0)),
            "skipped": int(summary.get("skipped", 0)),
            "failed": int(summary.get("failed", 0)),
            "suppressed_by_agent": len(result.get("suppressed") or []),
            "llm_errors": len(result.get("llm_errors") or []),
            "run_status": "completed",
            "execution_id": execution_id,
        }
