"""`LabLaunchWorkflow`: el botón "Nueva corrida" de punta a punta (plan §3.7).

Corre en Temporal Cloud con el worker `sales_eval` de producción. Fases:
exportar el banco → dejar la orden → prender la caja → darle la orden por
SSM → seguir el avance hasta que la caja termine. Candados:

* una corrida a la vez: id fijo `lab-launch` con política de conflicto FAIL;
* cancelar detiene la corrida (la caja guarda lo que alcanzó y se apaga sola);
* sin reintentos que gasten: si la caja no prende, la corrida queda fallida
  con el motivo y NO se da la orden;
* una orden repetida no duplica la corrida (la caja responde `already_dispatched`).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest
from temporalio import activity
from temporalio.client import WorkflowExecutionStatus
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.chats.agent.sales_lab.launch.contracts import (
    BenchInfo,
    DispatchInput,
    ExportBenchInput,
    LabLaunchInput,
    LabOrder,
    LabProgress,
    PollInput,
)
from src.plugins.chats.agent.sales_eval.workflows.lab_launch import LAB_LAUNCH_WORKFLOW_ID, LabLaunchWorkflow

QUEUE = "queue-lab-launch-test"


@dataclass
class Box:
    calls: list[str] = field(default_factory=list)
    start_fails: bool = False
    dispatch_answers: list[str] = field(default_factory=lambda: ["dispatched"])
    final_phase: str = "done"
    poll_delay_s: float = 0.0
    cancelled: bool = False
    spend_limits: list[float] = field(default_factory=list)
    poll_fails: bool = False


def _activities(box: Box):
    @activity.defn(name="lab_export_bench_snapshot")
    async def export(inp: ExportBenchInput) -> BenchInfo:
        box.calls.append(f"export:{inp.bench_id}")
        return BenchInfo(bench_id=inp.bench_id, sessions=82, customer_turns=309, files=410)

    @activity.defn(name="lab_read_bench_info")
    async def read_bench(bench_id: str) -> BenchInfo:
        box.calls.append(f"reuse:{bench_id}")
        return BenchInfo(bench_id=bench_id, sessions=80, customer_turns=300, files=400)

    @activity.defn(name="lab_write_order")
    async def write_order(order: LabOrder) -> None:
        box.calls.append(f"order:{order.run_id}:{order.bench_id}:{','.join(order.arms)}x{order.reps}")
        box.spend_limits.append(order.spend_limit_usd)

    @activity.defn(name="lab_start_box")
    async def start_box() -> None:
        box.calls.append("start")
        if box.start_fails:
            raise ApplicationError("la caja no prendió: InsufficientInstanceCapacity", non_retryable=True)

    @activity.defn(name="lab_dispatch_run")
    async def dispatch(inp: DispatchInput) -> str:
        answer = box.dispatch_answers.pop(0) if box.dispatch_answers else "already_dispatched"
        box.calls.append(f"dispatch:{answer}")
        return answer

    @activity.defn(name="lab_poll_run")
    async def poll(inp: PollInput) -> LabProgress:
        box.calls.append("poll")
        if box.poll_fails:
            raise ApplicationError("la caja dejó de reportar", non_retryable=True)
        waited = 0.0
        while waited < box.poll_delay_s and not box.cancelled:
            await asyncio.sleep(0.05)
            waited += 0.05
        phase = "cancelled" if box.cancelled else box.final_phase
        return LabProgress(run_id=inp.run_id, phase=phase, turns_done=10, turns_total=20, spent_usd=3.5)

    @activity.defn(name="lab_cancel_run")
    async def cancel(run_id: str) -> str:
        box.calls.append("cancel")
        box.cancelled = True
        return "cancel_requested"

    return [export, read_bench, write_order, start_box, dispatch, poll, cancel]


def _input(bench_id: str | None = None) -> LabLaunchInput:
    return LabLaunchInput(
        run_id="run-20260923-a1b2",
        arms=["A1", "B"],
        reps=1,
        bench_id=bench_id,
        image="ghcr.io/o/agencyhubara:abc",
        since_ms=1_789_000_000_000,
        estimate_usd=20.0,
        spend_limit_usd=120.0,
    )


async def _run(box: Box, inp: LabLaunchInput, *, during=None) -> dict:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue=QUEUE, workflows=[LabLaunchWorkflow], activities=_activities(box)):
            handle = await env.client.start_workflow(
                LabLaunchWorkflow.run, inp, id=LAB_LAUNCH_WORKFLOW_ID, task_queue=QUEUE,
                id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
            )
            if during is not None:
                await during(env, handle)
            return await handle.result()


@pytest.mark.asyncio
async def test_new_bench_run_goes_through_every_phase_in_order() -> None:
    box = Box()

    result = await _run(box, _input())

    assert box.calls == [
        "export:bench-run-20260923-a1b2",
        "order:run-20260923-a1b2:bench-run-20260923-a1b2:A1,Bx1",
        "start",
        "dispatch:dispatched",
        "poll",
    ]
    assert (result["phase"], result["bench_id"], result["turns_done"], result["spent_usd"]) == (
        "done", "bench-run-20260923-a1b2", 10, 3.5
    )
    assert box.spend_limits == [120.0]  # el tope real, no el estimado


@pytest.mark.asyncio
async def test_reusing_a_bench_skips_the_export() -> None:
    box = Box()

    result = await _run(box, _input(bench_id="bench-run-20260920-ffff"))

    assert box.calls[0] == "reuse:bench-run-20260920-ffff"
    assert not any(c.startswith("export") for c in box.calls)
    assert result["bench_id"] == "bench-run-20260920-ffff"


@pytest.mark.asyncio
async def test_a_second_launch_while_one_runs_is_refused() -> None:
    box = Box(poll_delay_s=5)

    async def second(env, handle) -> None:
        with pytest.raises(WorkflowAlreadyStartedError):
            await env.client.start_workflow(
                LabLaunchWorkflow.run, _input(), id=LAB_LAUNCH_WORKFLOW_ID, task_queue=QUEUE,
                id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
            )
        desc = await handle.describe()
        assert desc.status == WorkflowExecutionStatus.RUNNING

    await _run(box, _input(), during=second)
    assert box.calls.count("start") == 1


@pytest.mark.asyncio
async def test_cancel_asks_the_box_to_stop_and_ends_cancelled() -> None:
    box = Box(poll_delay_s=60)

    async def cancel(env, handle) -> None:
        for _ in range(100):
            status = await handle.query(LabLaunchWorkflow.status)
            if status["phase"] == "running":
                break
            await asyncio.sleep(0.05)
        await handle.signal(LabLaunchWorkflow.cancel)

    result = await _run(box, _input(), during=cancel)

    assert "cancel" in box.calls
    assert result["phase"] == "cancelled"


@pytest.mark.asyncio
async def test_box_that_does_not_start_fails_with_the_reason_and_no_order_is_sent() -> None:
    box = Box(start_fails=True)

    result = await _run(box, _input())

    assert result["phase"] == "failed"
    assert "no prendió" in result["error"]
    assert not any(c.startswith("dispatch") for c in box.calls)


@pytest.mark.asyncio
async def test_repeated_order_is_recognized_by_the_box_and_the_run_goes_on() -> None:
    box = Box(dispatch_answers=["already_dispatched"])

    result = await _run(box, _input())

    assert result["phase"] == "done"
    assert box.calls.count("poll") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("answer", "reason"), [("busy", "ocupada con otra corrida"), ("lost", "perdió esta corrida")])
async def test_a_box_that_refuses_the_order_fails_the_run_with_a_clear_reason(answer: str, reason: str) -> None:
    box = Box(dispatch_answers=[answer])

    result = await _run(box, _input())

    assert result["phase"] == "failed"
    assert reason in result["error"]
    assert "poll" not in box.calls


@pytest.mark.asyncio
async def test_when_following_the_run_fails_the_box_is_asked_to_stop() -> None:
    """Si seguir la corrida falla (la caja dejó de reportar, un deploy agotó
    los intentos), la caja puede seguir gastando: se le pide parar antes de
    soltar el candado `lab-launch` (si no, una corrida nueva entra en la misma
    caja)."""
    box = Box(poll_fails=True)

    result = await _run(box, _input())

    assert result["phase"] == "failed"
    assert box.calls[-1] == "cancel"


def test_sales_eval_worker_registers_the_launcher_and_every_activity_it_uses() -> None:
    """L-3: una activity que el workflow agenda y el worker no registra muere en
    RUNTIME (la corrida quedaría colgada), no en el arranque."""
    import ast
    import inspect

    from src.plugins.chats.agent.sales_eval.workflows import lab_launch as workflow
    from src.plugins.chats.agent.sales_lab.launch import activities
    from src.plugins.chats.workers import sales_eval

    source = inspect.getsource(sales_eval)
    assert "LabLaunchWorkflow" in source and "*LAB_LAUNCH_ACTIVITIES" in source
    registered = {a.__temporal_activity_definition.name for a in activities.LAB_LAUNCH_ACTIVITIES}
    scheduled = {
        node.args[0].value
        for node in ast.walk(ast.parse(inspect.getsource(workflow)))
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", "") in {"execute_activity", "start_activity"}
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert scheduled == registered
