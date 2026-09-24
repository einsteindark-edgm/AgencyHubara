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
  evaluating  scorecard en modo turno por brazo y repetición (A0 re-medido
              igual), con juez salvo que el gasto haya llegado al tope; después
              el resumen: gráficas, diferencias con intervalo, fidelidad y
              arena (PR 13)
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
        EvaluateInput,
        EvaluateResult,
        LabRunInput,
        ProgressUpdate,
        PublishResult,
        RunPlan,
        SimulateInput,
        SmokeResult,
        SummarizeInput,
        SummarizeResult,
    )
    from src.plugins.chats.agent.sales_lab.arms import SIMULATED_ARMS

_QUICK = {"start_to_close_timeout": timedelta(minutes=2), "retry_policy": RetryPolicy(maximum_attempts=3)}
#: Brazos que el simulador sabe correr: el bot actual (A1) y los bots nuevos
#: con Jev (B) y con OpenAI (C). Un brazo desconocido se informa, no se corre.
RUNNABLE_ARMS = SIMULATED_ARMS
ARMS_PENDING_NOTE = "{arms}: el simulador no conoce ese bot (corre A1, B y C)"
SPEND_CAP_NOTE = "la corrida se detuvo al llegar al tope de gasto"
NO_JUDGE_NOTE = "calificada sin juez (solo checks de código): el gasto ya estaba en el tope"
CONTROL = "A0"
CONCURRENCY = 4
_EVALUATE = {
    "start_to_close_timeout": timedelta(hours=2),
    "heartbeat_timeout": timedelta(minutes=2),
    "retry_policy": RetryPolicy(maximum_attempts=2),
}
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
            spent = 0.0
            if simulated:
                smoke = await workflow.execute_activity(
                    "lab_run_smoke_turn",
                    plan,
                    result_type=SmokeResult,
                    start_to_close_timeout=timedelta(minutes=15),
                    heartbeat_timeout=timedelta(minutes=2),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
                spent += smoke.cost_usd
                if not smoke.ok:
                    error = f"el turno de humo no pasó ({smoke.case_id}): {smoke.error}"[:500]
                    await progress(ProgressUpdate(run_id=inp.run_id, phase="failed", error=error, spent_usd=spent))
                    return {"phase": "failed", "error": error}
            runnable = [a for a in simulated if a in RUNNABLE_ARMS]
            pending = [a for a in simulated if a not in RUNNABLE_ARMS]
            notes = [ARMS_PENDING_NOTE.format(arms=", ".join(pending))] if pending else []
            if not runnable:
                await progress(ProgressUpdate(run_id=inp.run_id, phase="done", turns_done=published.cases,
                                              turns_total=published.cases, spent_usd=spent, notes=notes))
                return {"phase": "done", "cases": published.cases, "notes": notes}
            outcome = await self._simulate(inp.run_id, plan, runnable, published.cases, spent, notes, progress)
            if outcome["phase"] == "cancelled":
                return outcome
            return await self._evaluate(inp.run_id, plan, runnable, outcome, progress)
        except ActivityError as exc:
            cause = exc.cause if exc.cause is not None else exc
            error = str(getattr(cause, "message", None) or cause)[:500]
            await progress(ProgressUpdate(run_id=inp.run_id, phase="failed", error=error))
            return {"phase": "failed", "error": error}


    async def _simulate(self, run_id, plan, arms, cases, spent, notes, progress) -> dict:
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
                                                      turns_total=total, spent_usd=spent, notes=notes))
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
                        spent += result.cost_usd
                        if result.ok:
                            done += 1
                        else:
                            failed += 1
                    await progress(ProgressUpdate(run_id=run_id, phase="running", turns_done=done,
                                                  turns_total=total, spent_usd=round(spent, 6), notes=notes))
                    if plan.spend_limit_usd > 0 and spent >= plan.spend_limit_usd:
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
        return {"phase": "simulated", "cases": cases, "turns_done": done, "turns_total": total,
                "spent_usd": round(spent, 6), "stopped": stopped, "notes": final_notes}

    async def _evaluate(self, run_id, plan, arms, outcome, progress) -> dict:
        """Califica cada brazo en modo turno (A0 una vez; los simulados, cada
        repetición) y publica el resumen. Sin juez si el gasto llegó al tope."""
        notes = list(outcome["notes"])
        judge = not outcome["stopped"]
        if not judge:
            notes.append(NO_JUDGE_NOTE)
        state = {k: outcome[k] for k in ("turns_done", "turns_total", "spent_usd")}
        await progress(ProgressUpdate(run_id=run_id, phase="evaluating", notes=notes, **state))
        for arm in [CONTROL, *arms]:
            for rep in range(1 if arm == CONTROL else plan.reps):
                if await workflow.execute_activity("lab_run_cancel_requested", run_id, result_type=bool, **_QUICK):
                    await progress(ProgressUpdate(run_id=run_id, phase="cancelled", notes=notes, **state))
                    return {"phase": "cancelled"}
                await workflow.execute_activity(
                    "lab_run_evaluate_arm",
                    EvaluateInput(run_id=run_id, bench_id=plan.bench_id, arm=arm, rep=rep, judge=judge),
                    result_type=EvaluateResult,
                    **_EVALUATE,
                )
        summary = await workflow.execute_activity(
            "lab_run_summarize",
            SummarizeInput(run_id=run_id, arms=[CONTROL, *arms], reps=plan.reps),
            result_type=SummarizeResult,
            start_to_close_timeout=timedelta(minutes=20),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        notes.extend(summary.notes)
        await progress(ProgressUpdate(run_id=run_id, phase="done", notes=notes, **state))
        return {"phase": "done", "cases": outcome["cases"], "turns_done": state["turns_done"], "notes": notes}

    async def _publish(self, run_id: str, arm: str, rep: int) -> None:
        await workflow.execute_activity(
            "lab_run_publish_arm",
            ArmPublishInput(run_id=run_id, arm=arm, rep=rep),
            result_type=ArmPublishResult,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
