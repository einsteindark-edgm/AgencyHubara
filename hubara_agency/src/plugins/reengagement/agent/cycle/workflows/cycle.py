"""ReengagementCycleWorkflow — UN ciclo del Window Strategist.

snapshot (vault) → dispatch a la caja GraphAgents → poll hasta terminal →
un `ReengagementDispatchIntentEvent` por intent. La cadencia entre ciclos la
da un Temporal Schedule (deploy); el workflow es one-shot (no loop eterno).

R-DET: cero I/O acá — todo side-effect es activity; el poll loop usa
`workflow.sleep` (durable). El workflow NUNCA manda mensajes: el remarketing
(transition declarativa + gate `check_reengagement_policy`) toca al cliente.
"""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from src.plugins.reengagement.agent.cycle.activities import (
        build_reengagement_snapshot_activity,
        dispatch_window_strategist_activity,
        poll_window_strategist_activity,
    )
    from src.plugins.reengagement.shared.contracts.events import (
        ReengagementDispatchIntentEvent,
    )
    from src.sdk.eventkit import dispatch_event_activity, envelope_for

#: intervalo entre polls del run (la caja tarda segundos-minutos por ciclo).
_POLL_INTERVAL = timedelta(seconds=15)

#: tope de polls por ciclo (~15 min) — un run colgado NO cuelga el ciclo.
_MAX_POLLS = 60


@workflow.defn(name="ReengagementCycleWorkflow")
class ReengagementCycleWorkflow:
    @workflow.run
    async def run(self) -> dict:
        snapshot = await workflow.execute_activity(
            build_reengagement_snapshot_activity,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        if not snapshot.get("conversations"):
            workflow.logger.info(
                "ReengagementCycle: snapshot vacío — no se paga cold start."
            )
            return {"dispatched": 0, "suppressed": 0, "run_status": "skipped_empty"}

        run_id = f"{workflow.info().workflow_id}:{workflow.info().run_id}"
        execution_id = await workflow.execute_activity(
            dispatch_window_strategist_activity,
            args=[snapshot, run_id],
            # cold start EC2 (1-3 min) + dispatch SSM — generoso a propósito.
            start_to_close_timeout=timedelta(minutes=10),
            heartbeat_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )

        state: dict = {"status": "running"}
        for _ in range(_MAX_POLLS):
            state = await workflow.execute_activity(
                poll_window_strategist_activity,
                args=[execution_id],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            if state.get("status") in ("completed", "failed"):
                break
            await workflow.sleep(_POLL_INTERVAL)

        if state.get("status") != "completed":
            workflow.logger.warning(
                "ReengagementCycle: run %s terminó en %s (%s) — sin dispatch.",
                execution_id,
                state.get("status"),
                state.get("error"),
            )
            return {
                "dispatched": 0,
                "suppressed": 0,
                "run_status": str(state.get("status")),
                "execution_id": execution_id,
            }

        # PM-001 (premortem order-sentinel): el output de un agente DIRECTO es
        # el STATE completo del grafo — el contrato vive un nivel adentro. Con
        # la extracción vieja, dispatched=0 con run completed PARA SIEMPRE.
        result = extract_agent_result(state.get("result"))
        if result is None:
            workflow.logger.error(
                "ReengagementCycle: run %s completed pero el result no trae "
                "`dispatch` en ningún nivel (¿podado por --compact?) — sin "
                "emitir intents.",
                execution_id,
            )
            return {
                "dispatched": 0,
                "suppressed": 0,
                "run_status": "result_missing_dispatch",
                "execution_id": execution_id,
            }
        intents = result.get("dispatch") or []
        for intent in intents:
            event = ReengagementDispatchIntentEvent(
                session_id=intent["session_id"],
                reason=intent.get("reason", ""),
                recommended_channel=intent.get("recommended_channel", ""),
                recommended_category=intent.get("recommended_category", ""),
                motivo=(
                    "Window Strategist: reactivación "
                    f"({intent.get('reason', 'sin razón')})"
                ),
                run_id=run_id,
                priority=int(intent.get("priority", 0)),
            )
            await workflow.execute_activity(
                dispatch_event_activity,
                envelope_for(
                    event, source_plugin="reengagement", source_worker="cycle"
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

        return {
            "dispatched": len(intents),
            "suppressed": len(result.get("suppressed") or []),
            "truncated_by_budget": int(result.get("truncated_by_budget") or 0),
            "run_status": "completed",
            "execution_id": execution_id,
        }
