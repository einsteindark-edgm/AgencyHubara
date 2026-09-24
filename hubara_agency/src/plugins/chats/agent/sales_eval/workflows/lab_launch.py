"""`LabLaunchWorkflow`: lo que pasa al pulsar "Nueva corrida" (plan §3.7).

Corre en Temporal Cloud, en el worker `sales_eval` de producción:

  1. exportar el banco (o reusar uno) · 2. dejar la orden en `orders/` ·
  3. prender la caja · 4. darle la orden por SSM · 5. seguir el avance.

Id FIJO (`lab-launch`) + política de conflicto FAIL en el `start_workflow` de
la API: una corrida a la vez (un doble clic o dos personas → 409). Durable: si
producción se redespliega, sigue donde iba. Sin reintentos que gasten: una
caja que no prende deja la corrida fallida con el motivo; nada se relanza solo.
`cancel` pide a la caja que pare; la caja guarda lo que alcanzó y se apaga sola.
"""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from src.plugins.chats.agent.sales_lab.launch.contracts import (
        TERMINAL_PHASES,
        BenchInfo,
        DispatchInput,
        ExportBenchInput,
        LabLaunchInput,
        LabOrder,
        LabProgress,
        PollInput,
    )

LAB_LAUNCH_WORKFLOW_ID = "lab-launch"
_CANCEL_GRACE = timedelta(minutes=20)


def _error_text(exc: BaseException) -> str:
    cause = exc.cause if isinstance(exc, ActivityError) and exc.cause is not None else exc
    return str(getattr(cause, "message", None) or cause)[:500]


@workflow.defn(name="LabLaunchWorkflow")
class LabLaunchWorkflow:
    def __init__(self) -> None:
        self._cancel = False
        self._status: dict = {"phase": "queued"}

    @workflow.signal
    def cancel(self) -> None:
        self._cancel = True

    @workflow.query
    def status(self) -> dict:
        return dict(self._status)

    def _phase(self, phase: str, **extra) -> None:
        self._status = {**self._status, "phase": phase, **extra}

    @workflow.run
    async def run(self, inp: LabLaunchInput) -> dict:
        self._status = {
            "phase": "exporting",
            "run_id": inp.run_id,
            "arms": list(inp.arms),
            "reps": inp.reps,
            "bench_id": inp.bench_id,
            "estimate_usd": inp.estimate_usd,
            "started_at_ms": int(workflow.now().timestamp() * 1000),
            "error": None,
        }
        try:
            bench = await self._bench(inp)
            self._phase("ordering", bench_id=bench.bench_id, turns_total=bench.customer_turns * len(inp.arms) * inp.reps)
            if self._cancel:
                self._phase("cancelled")
                return self.status()
            await workflow.execute_activity(
                "lab_write_order",
                LabOrder(
                    run_id=inp.run_id, bench_id=bench.bench_id, arms=list(inp.arms), reps=inp.reps,
                    image=inp.image, estimate_usd=inp.estimate_usd, spend_limit_usd=inp.spend_limit_usd,
                    requested_at_ms=self._status["started_at_ms"],
                ),
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            self._phase("starting_box")
            await workflow.execute_activity(
                "lab_start_box",
                start_to_close_timeout=timedelta(minutes=10),
                heartbeat_timeout=timedelta(minutes=2),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
            if self._cancel:
                self._phase("cancelled")
                return self.status()
            self._phase("dispatching")
            answer = await workflow.execute_activity(
                "lab_dispatch_run",
                DispatchInput(run_id=inp.run_id, image=inp.image),
                result_type=str,
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            if answer not in ("dispatched", "already_dispatched"):
                raise RuntimeError(f"la caja no aceptó la orden: {answer!r}")
            self._phase("running")
            progress = await self._follow(inp.run_id)
            self._phase(
                progress.phase if progress.phase in TERMINAL_PHASES else "failed",
                turns_done=progress.turns_done,
                turns_total=progress.turns_total or self._status.get("turns_total"),
                spent_usd=progress.spent_usd,
                error=progress.error,
            )
        except (ActivityError, RuntimeError) as exc:
            self._phase("failed", error=_error_text(exc))
        return self.status()

    async def _bench(self, inp: LabLaunchInput) -> BenchInfo:
        if inp.bench_id:
            return await workflow.execute_activity(
                "lab_read_bench_info",
                inp.bench_id,
                result_type=BenchInfo,
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
        return await workflow.execute_activity(
            "lab_export_bench_snapshot",
            ExportBenchInput(bench_id=f"bench-{inp.run_id}", since_ms=inp.since_ms),
            result_type=BenchInfo,
            start_to_close_timeout=timedelta(minutes=30),
            heartbeat_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

    async def _follow(self, run_id: str) -> LabProgress:
        poll = workflow.start_activity(
            "lab_poll_run",
            PollInput(run_id=run_id),
            result_type=LabProgress,
            start_to_close_timeout=timedelta(hours=8),
            heartbeat_timeout=timedelta(minutes=3),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        await workflow.wait_condition(lambda: self._cancel or poll.done())
        if self._cancel and not poll.done():
            self._phase("cancelling")
            await workflow.execute_activity(
                "lab_cancel_run",
                run_id,
                result_type=str,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            try:
                await workflow.wait_condition(poll.done, timeout=_CANCEL_GRACE)
            except TimeoutError:
                poll.cancel()
                return LabProgress(run_id=run_id, phase="cancelled", error="la caja no confirmó la cancelación")
        return await poll
