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
              igual), por pedazos de sesiones: cada pedazo deja su avance (el
              lanzador da la corrida por caída a los 20 min sin noticias) y
              suma el gasto del juez. Con juez salvo que el gasto haya llegado
              al tope o el juez no quepa en lo que queda; después el resumen:
              gráficas, diferencias con intervalo, fidelidad y arena (PR 13)
  done | failed | cancelled

Un bot que la corrida no alcanzó a simular (tope de gasto, límite de
historia) queda pendiente: no se califica ni se compara.
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
NO_JUDGE_FIT_NOTE = "calificada sin juez (solo checks de código): el juez (≈US${usd:.2f}) no cabía en lo que quedaba del tope"
NOT_SIMULATED_NOTE = "{arms}: la corrida no alcanzó a simularlo (queda pendiente, no cuenta como falla)"
EVALUATION_HISTORY_NOTE = "la calificación se detuvo cerca del límite de historia de Temporal: {arms} sin calificar"
CONTROL = "A0"
CONCURRENCY = 4
_EVALUATE = {  # un pedazo (≤ 12 turnos por defecto): ~3–5 min con juez
    "start_to_close_timeout": timedelta(minutes=30),
    "heartbeat_timeout": timedelta(minutes=2),
    "retry_policy": RetryPolicy(maximum_attempts=3),
}
# Un caso que murió sin reportar su costo (timeout, proceso matado) igual gastó
# LLM: se le carga la tarifa medida por turno (`launch.costs.AGENT_USD_PER_TURN`,
# US$7,09 en ~404 turnos) para que el tope nunca se subcuente.
UNREPORTED_CASE_USD = 7.09 / 404
# El juez, a la tarifa medida por turno calificado (`launch.costs.JUDGE_USD_PER_TURN`):
# con ella se decide si la pasada del juez cabe en lo que queda del tope.
JUDGE_TURN_USD = 30.0 / (9 * 404)
# Temporal corta un workflow en 51.200 eventos (un caso simulado deja ~9). Al
# llegar a `LabRunInput.history_limit` la corrida deja de simular y califica lo
# que alcanzó; la calificación se detiene (entre repeticiones) con este margen más.
HISTORY_NOTE = "la corrida dejó de simular cerca del límite de historia de Temporal"
EVALUATION_HISTORY_HEADROOM = 8_000
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
        self._history_limit = inp.history_limit
        # Repeticiones que se alcanzaron a simular, por bot.
        self._simulated: dict[str, int] = {}
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
            if outcome["phase"] == "cancelled":
                return outcome
            return await self._evaluate(inp.run_id, plan, runnable, outcome, progress)
        except ActivityError as exc:
            cause = exc.cause if exc.cause is not None else exc
            error = str(getattr(cause, "message", None) or cause)[:500]
            await progress(ProgressUpdate(run_id=inp.run_id, phase="failed", error=error, spent_usd=round(self._spent, 6)))
            return {"phase": "failed", "error": error}


    async def _simulate(self, run_id, plan, arms, cases, notes, progress) -> dict:
        """Corre los brazos simulados: cada caso × repetición en su sandbox, de
        a `CONCURRENCY`. Entre lotes: cancelación, tope de gasto y límite de
        historia. Al terminar cada repetición se publica lo que corrió."""
        total = cases * plan.reps * len(arms)
        done = 0
        failed = 0
        stopped = False
        out_of_history = False
        for arm in arms:
            for rep in range(plan.reps):
                self._simulated[arm] = rep + 1
                for start in range(0, cases, CONCURRENCY):
                    if await workflow.execute_activity("lab_run_cancel_requested", run_id, result_type=bool, **_QUICK):
                        await self._publish(run_id, arm, rep)
                        await progress(ProgressUpdate(run_id=run_id, phase="cancelled", turns_done=done,
                                                      turns_total=total, spent_usd=round(self._spent, 6), notes=notes))
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
                    elif workflow.info().get_current_history_length() >= self._history_limit:
                        out_of_history = True
                    if stopped or out_of_history:
                        break
                await self._publish(run_id, arm, rep)
                if stopped or out_of_history:
                    break
            if stopped or out_of_history:
                break
        final_notes = list(notes)
        if failed:
            final_notes.append(f"{failed} {'caso sin terminar' if failed == 1 else 'casos sin terminar'} (ver sus errores)")
        if stopped:
            final_notes.append(SPEND_CAP_NOTE)
        if out_of_history:
            final_notes.append(HISTORY_NOTE)
        never = [a for a in arms if not self._simulated.get(a)]
        if never:
            final_notes.append(NOT_SIMULATED_NOTE.format(arms=", ".join(never)))
        return {"phase": "simulated", "cases": cases, "turns_done": done, "turns_total": total,
                "spent_usd": round(self._spent, 6), "stopped": stopped, "notes": final_notes}

    async def _evaluate(self, run_id, plan, arms, outcome, progress) -> dict:
        """Califica cada brazo simulado en modo turno (A0 una vez; los
        simulados, cada repetición que se alcanzó a simular) por pedazos, y
        publica el resumen. Sin juez si el gasto llegó al tope o si la pasada
        del juez (tarifa medida por turno) no cabe en lo que queda: igual para
        todos los bots, para que se comparen con la misma vara."""
        notes = list(outcome["notes"])
        reps_of = {CONTROL: 1, **{a: self._simulated.get(a, 0) for a in arms}}
        judge_usd = JUDGE_TURN_USD * (outcome["cases"] + outcome["turns_done"])
        judge = not outcome["stopped"] and (
            plan.spend_limit_usd <= 0 or self._spent + judge_usd <= plan.spend_limit_usd
        )
        if outcome["stopped"]:
            notes.append(NO_JUDGE_NOTE)
        elif not judge:
            notes.append(NO_JUDGE_FIT_NOTE.format(usd=judge_usd))
        state = {k: outcome[k] for k in ("turns_done", "turns_total")}

        async def report(phase: str) -> None:
            await progress(ProgressUpdate(run_id=run_id, phase=phase, notes=notes, spent_usd=round(self._spent, 6), **state))

        await report("evaluating")
        evaluated: dict[str, int] = {}
        order = [(arm, rep) for arm in [CONTROL, *arms] for rep in range(reps_of[arm])]
        for position, (arm, rep) in enumerate(order):
            if workflow.info().get_current_history_length() >= self._history_limit + EVALUATION_HISTORY_HEADROOM:
                left = sorted({a for a, _ in order[position:]}, key=[CONTROL, *arms].index)
                notes.append(EVALUATION_HISTORY_NOTE.format(arms=", ".join(left)))
                break
            offset: int | None = 0
            while offset is not None:
                if await workflow.execute_activity("lab_run_cancel_requested", run_id, result_type=bool, **_QUICK):
                    await report("cancelled")
                    return {"phase": "cancelled"}
                result = await workflow.execute_activity(
                    "lab_run_evaluate_arm",
                    EvaluateInput(run_id=run_id, bench_id=plan.bench_id, arm=arm, rep=rep, judge=judge, offset=offset),
                    result_type=EvaluateResult,
                    **_EVALUATE,
                )
                self._spent += result.judge_usd
                await report("evaluating")
                offset = result.next_offset
            evaluated[arm] = rep + 1
        scored = [a for a in [CONTROL, *arms] if evaluated.get(a)]
        summary = await workflow.execute_activity(
            "lab_run_summarize",
            SummarizeInput(run_id=run_id, arms=scored, reps=plan.reps, reps_by_arm=evaluated,
                           arms_pending=[a for a in arms if not evaluated.get(a)]),
            result_type=SummarizeResult,
            start_to_close_timeout=timedelta(minutes=20),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        notes.extend(summary.notes)
        await report("done")
        return {"phase": "done", "cases": outcome["cases"], "turns_done": state["turns_done"],
                "spent_usd": round(self._spent, 6), "notes": notes}

    async def _publish(self, run_id: str, arm: str, rep: int) -> None:
        await workflow.execute_activity(
            "lab_run_publish_arm",
            ArmPublishInput(run_id=run_id, arm=arm, rep=rep),
            result_type=ArmPublishResult,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
