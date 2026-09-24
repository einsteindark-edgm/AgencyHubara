"""`LabRunWorkflow`: una corrida del laboratorio, en el Temporal de la CAJA.

Namespace `hubara-lab`, cola `queue-sales-lab`: nada de esto existe en
Temporal Cloud. El Temporal de la caja se borra en cada arranque, así que este
workflow no tiene historias vivas que proteger con `patched`.

Fases (las ve el lanzador en `runs/<corrida>/progress.json`):
  preparing   baja la orden y el banco
  running     arma los casos y publica el control real (A0); si hay brazos
              simulados, un turno de humo del banco tiene que pasar antes
              (PR 11) y después corren A1 (PR 13) y los bots nuevos B y C
              (PR 15): cada caso × repetición en su sandbox, con tope de gasto
  evaluating  scorecard por turno y juez (PR 12+)
  done | failed | cancelled
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from src.plugins.chats.agent.sales_lab.run.contracts import (
        ArmPublishInput,
        ArmPublishResult,
        CaseOutcome,
        LabRunInput,
        ProgressUpdate,
        PublishResult,
        RunPlan,
        SimulateInput,
        SmokeResult,
    )
    from src.plugins.chats.agent.sales_lab.arms import SIMULATED_ARMS

_QUICK = {"start_to_close_timeout": timedelta(minutes=2), "retry_policy": RetryPolicy(maximum_attempts=3)}
#: Brazos que el simulador sabe correr: el bot actual (A1) y los bots nuevos
#: con Jev (B) y con OpenAI (C). Un brazo desconocido se informa, no se corre.
RUNNABLE_ARMS = SIMULATED_ARMS
ARMS_PENDING_NOTE = "{arms}: el simulador no conoce ese bot (corre A1, B y C)"
SPEND_CAP_NOTE = "la corrida se detuvo al llegar al tope de gasto"
CONCURRENCY = 4
# Un caso que murió sin reportar su costo (timeout, proceso matado) igual gastó
# LLM: se le carga la tarifa medida por turno (`launch.costs.AGENT_USD_PER_TURN`,
# US$7,09 en ~404 turnos) para que el tope nunca se subcuente.
UNREPORTED_CASE_USD = 7.09 / 404
_CASE = {
    "start_to_close_timeout": timedelta(minutes=20),
    "heartbeat_timeout": timedelta(minutes=2),
    "retry_policy": RetryPolicy(maximum_attempts=2),
}


@workflow.defn(name="LabRunWorkflow")
class LabRunWorkflow:
    @workflow.run
    async def run(self, inp: LabRunInput) -> dict:
        async def progress(update: ProgressUpdate) -> None:
            await workflow.execute_activity("lab_run_progress", update, **_QUICK)

        # Lo gastado vive en el estado: un fallo a mitad lo reporta igual (el tope
        # del mes en producción suma `spent_usd` de progress.json).
        self._spent = 0.0
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
            if simulated:
                smoke = await workflow.execute_activity(
                    "lab_run_smoke_turn",
                    plan,
                    result_type=SmokeResult,
                    start_to_close_timeout=timedelta(minutes=15),
                    heartbeat_timeout=timedelta(minutes=2),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
                self._spent += smoke.cost_usd
                if not smoke.ok:
                    error = f"el turno de humo no pasó ({smoke.case_id}): {smoke.error}"[:500]
                    await progress(ProgressUpdate(run_id=inp.run_id, phase="failed", error=error, spent_usd=self._spent))
                    return {"phase": "failed", "error": error}
            runnable = [a for a in simulated if a in RUNNABLE_ARMS]
            pending = [a for a in simulated if a not in RUNNABLE_ARMS]
            notes = [ARMS_PENDING_NOTE.format(arms=", ".join(pending))] if pending else []
            if not runnable:
                await progress(ProgressUpdate(run_id=inp.run_id, phase="done", turns_done=published.cases,
                                              turns_total=published.cases, spent_usd=self._spent, notes=notes))
                return {"phase": "done", "cases": published.cases, "notes": notes}
            outcome = await self._simulate(inp.run_id, plan, runnable, published.cases, notes, progress)
            return outcome
        except ActivityError as exc:
            cause = exc.cause if exc.cause is not None else exc
            error = str(getattr(cause, "message", None) or cause)[:500]
            await progress(ProgressUpdate(run_id=inp.run_id, phase="failed", error=error, spent_usd=round(self._spent, 6)))
            return {"phase": "failed", "error": error}


    async def _simulate(self, run_id, plan, arms, cases, notes, progress) -> dict:
        """Corre los brazos simulados: cada caso × repetición en su sandbox, de
        a `CONCURRENCY`. Entre lotes: cancelación y tope de gasto. Al terminar
        cada repetición se publica lo que corrió."""
        total = cases * plan.reps * len(arms)
        done = 0
        failed = 0
        stopped = False
        for arm in arms:
            for rep in range(plan.reps):
                for start in range(0, cases, CONCURRENCY):
                    if await workflow.execute_activity("lab_run_cancel_requested", run_id, result_type=bool, **_QUICK):
                        await self._publish(run_id, arm, rep)
                        await progress(ProgressUpdate(run_id=run_id, phase="cancelled", turns_done=done,
                                                      turns_total=total, spent_usd=self._spent, notes=notes))
                        return {"phase": "cancelled"}
                    batch = [
                        workflow.execute_activity(
                            "lab_run_simulate_case",
                            SimulateInput(run_id=run_id, bench_id=plan.bench_id, arm=arm, rep=rep, index=i),
                            result_type=CaseOutcome,
                            **_CASE,
                        )
                        for i in range(start, min(start + CONCURRENCY, cases))
                    ]
                    for result in await asyncio.gather(*batch):
                        self._spent += result.cost_usd if result.ok or result.cost_usd > 0 else UNREPORTED_CASE_USD
                        if result.ok:
                            done += 1
                        else:
                            failed += 1
                    await progress(ProgressUpdate(run_id=run_id, phase="running", turns_done=done,
                                                  turns_total=total, spent_usd=round(self._spent, 6), notes=notes))
                    if plan.spend_limit_usd > 0 and self._spent >= plan.spend_limit_usd:
                        stopped = True
                        break
                await self._publish(run_id, arm, rep)
                if stopped:
                    break
            if stopped:
                break
        final_notes = list(notes)
        if failed:
            final_notes.append(f"{failed} {'caso sin terminar' if failed == 1 else 'casos sin terminar'} (ver sus errores)")
        if stopped:
            final_notes.append(SPEND_CAP_NOTE)
        await progress(ProgressUpdate(run_id=run_id, phase="done", turns_done=done, turns_total=total,
                                      spent_usd=round(self._spent, 6), notes=final_notes))
        return {"phase": "done", "cases": cases, "turns_done": done, "notes": final_notes}

    async def _publish(self, run_id: str, arm: str, rep: int) -> None:
        await workflow.execute_activity(
            "lab_run_publish_arm",
            ArmPublishInput(run_id=run_id, arm=arm, rep=rep),
            result_type=ArmPublishResult,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
