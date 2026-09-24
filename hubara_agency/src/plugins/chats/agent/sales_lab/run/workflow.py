"""`LabRunWorkflow`: una corrida del laboratorio, en el Temporal de la CAJA.

Namespace `hubara-lab`, cola `queue-sales-lab`: nada de esto existe en
Temporal Cloud. El Temporal de la caja se borra en cada arranque, así que este
workflow no tiene historias vivas que proteger con `patched`.

Fases (las ve el lanzador en `runs/<corrida>/progress.json`):
  preparing   baja la orden y el banco
  running     arma los casos y publica el control real (A0); con el
              simulador (PR 11+) corre los brazos A1, B y C
  evaluating  scorecard por turno y juez (PR 12+)
  done | failed | cancelled
"""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from src.plugins.chats.agent.sales_lab.run.contracts import (
        LabRunInput,
        ProgressUpdate,
        PublishResult,
        RunPlan,
    )

_QUICK = {"start_to_close_timeout": timedelta(minutes=2), "retry_policy": RetryPolicy(maximum_attempts=3)}
SIMULATION_PENDING_NOTE = "los brazos simulados (A1, B y C) corren desde el PR 11 del plan"


@workflow.defn(name="LabRunWorkflow")
class LabRunWorkflow:
    @workflow.run
    async def run(self, inp: LabRunInput) -> dict:
        async def progress(update: ProgressUpdate) -> None:
            await workflow.execute_activity("lab_run_progress", update, **_QUICK)

        await progress(ProgressUpdate(run_id=inp.run_id, phase="preparing"))
        try:
            plan = await workflow.execute_activity(
                "lab_run_prepare",
                inp.run_id,
                result_type=RunPlan,
                start_to_close_timeout=timedelta(minutes=20),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            if await workflow.execute_activity("lab_run_cancel_requested", inp.run_id, result_type=bool, **_QUICK):
                await progress(ProgressUpdate(run_id=inp.run_id, phase="cancelled"))
                return {"phase": "cancelled"}
            await progress(ProgressUpdate(run_id=inp.run_id, phase="running"))
            published = await workflow.execute_activity(
                "lab_run_publish_control",
                plan,
                result_type=PublishResult,
                start_to_close_timeout=timedelta(minutes=20),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            simulated = [a for a in plan.arms if a != "A0"]
            notes = [SIMULATION_PENDING_NOTE] if simulated else []
            await progress(
                ProgressUpdate(
                    run_id=inp.run_id,
                    phase="done",
                    turns_done=published.cases,
                    turns_total=published.cases,
                    notes=notes,
                )
            )
            return {"phase": "done", "cases": published.cases, "notes": notes}
        except ActivityError as exc:
            cause = exc.cause if exc.cause is not None else exc
            error = str(getattr(cause, "message", None) or cause)[:500]
            await progress(ProgressUpdate(run_id=inp.run_id, phase="failed", error=error))
            return {"phase": "failed", "error": error}
