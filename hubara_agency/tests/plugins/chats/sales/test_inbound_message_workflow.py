"""`InboundMessageWorkflow` (ADR 2026-10-15): la entrada durable de un mensaje
del cliente. El webhook lo arranca ANTES de responderle 200 a Meta; cada paso
del ingest corre en su activity con reintentos acotados. Un mensaje encadenado
al anterior del mismo cliente (mismo POST) espera a que ese termine antes de
escribir su historial: el orden de siempre, ahora sin depender de un proceso
vivo y sin frenar sus lecturas.

Los pasos del ingest son FAKES (`Steps`, registrados por nombre); la espera del
anterior es la real (le pregunta a Temporal por el workflow de ese mensaje).
Los runs de la fase 1 (el ingest entero en una activity) se prueban por replay
(`tests/test_replay_inbound_message.py`)."""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from temporalio import workflow
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from src.plugins.chats.agent.sales.activities.inbound_entry import wait_inbound_workflow_activity
from src.plugins.chats.agent.sales.contracts import InboundMessageInput, inbound_workflow_id
from src.plugins.chats.agent.sales.workflows.inbound_message import InboundMessageWorkflow
from tests.plugins.chats.sales.test_inbound_message_steps_workflow import QUEUE, Steps, _input


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        yield env


def _worker(env: WorkflowEnvironment, steps: Steps) -> Worker:
    fakes = [a for a in steps.activities() if a.__temporal_activity_definition.name != "wait_inbound_workflow"]  # type: ignore[attr-defined]
    return Worker(
        env.client, task_queue=QUEUE, workflows=[InboundMessageWorkflow],
        activities=[*fakes, wait_inbound_workflow_activity],
    )


async def _start(env: WorkflowEnvironment, inp: InboundMessageInput):
    return await env.client.start_workflow(
        InboundMessageWorkflow.run, inp, id=inbound_workflow_id(inp.message["message_id"]), task_queue=QUEUE
    )


@pytest.mark.asyncio
async def test_a_message_waits_for_the_previous_one_of_the_same_post(env) -> None:
    steps = Steps()
    steps.slow[("deliver", "wamid.A")] = 1.0
    async with _worker(env, steps):
        first = await _start(env, _input("wamid.A"))
        second = await _start(env, _input("wamid.B", after="wamid.A"))
        await asyncio.wait_for(asyncio.gather(first.result(), second.result()), timeout=60)

    assert steps.calls.index("deliver:wamid.A") < steps.calls.index("history:wamid.B")


@pytest.mark.asyncio
async def test_a_missing_previous_message_does_not_hold_the_next(env) -> None:
    steps = Steps()
    async with _worker(env, steps):
        result = await asyncio.wait_for((await _start(env, _input("wamid.B", after="wamid.GHOST"))).result(), timeout=60)

    assert result == "ingested"
    assert "deliver:wamid.B" in steps.calls


@pytest.mark.asyncio
async def test_a_previous_message_that_failed_does_not_hold_the_next(env) -> None:
    steps = Steps()
    steps.broken.add("state:wamid.A")
    async with _worker(env, steps):
        first = await _start(env, _input("wamid.A"))
        second = await _start(env, _input("wamid.B", after="wamid.A"))
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(first.result(), timeout=60)
        assert await asyncio.wait_for(second.result(), timeout=60) == "ingested"


@pytest.mark.asyncio
async def test_a_step_that_keeps_failing_gives_up_after_five_attempts_spread_over_minutes(env) -> None:
    """Acotado, pero no de segundos: un vault o un Temporal que tarda un par de
    minutos en volver no debe dejar el mensaje en Failed (Meta ya tiene su 200
    y no lo re-entrega)."""

    class _AlwaysFailing(Steps):
        async def _step(self, name, request):  # noqa: ANN001, ANN202
            if name == "state":
                self.calls.append("state:attempt")
                from temporalio.exceptions import ApplicationError

                raise ApplicationError("vault roto")
            return await super()._step(name, request)

    steps = _AlwaysFailing()
    async with _worker(env, steps):
        handle = await _start(env, _input("wamid.A"))
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(handle.result(), timeout=60)
        history = await handle.fetch_history()

    assert steps.calls.count("state:attempt") == 5
    elapsed = history.events[-1].event_time.ToSeconds() - history.events[0].event_time.ToSeconds()
    assert elapsed >= 120


@pytest.mark.asyncio
async def test_a_programming_error_is_not_retried(env) -> None:
    """Premortem PM-011: un error determinista (TypeError, KeyError…) no se
    arregla reintentando, y cada intento repetía Jev, el cupón en Medusa, el
    reloj del watchdog y analytics."""

    class _Buggy(Steps):
        async def _step(self, name, request):  # noqa: ANN001, ANN202
            if name == "state":
                self.calls.append("state:attempt")
                raise KeyError("campo_que_no_esta")
            return await super()._step(name, request)

    steps = _Buggy()
    async with _worker(env, steps):
        handle = await _start(env, _input("wamid.A"))
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(handle.result(), timeout=60)

    assert steps.calls.count("state:attempt") == 1


@workflow.defn(name="ProbeInboundWorkflowWait")
class _ProbeWait:
    """Sonda: devuelve lo que contesta la espera al anterior (el workflow real
    lo descarta y sigue, así que sus tests no ven la rama)."""

    @workflow.run
    async def run(self, workflow_id: str) -> str:
        return await workflow.execute_activity(
            wait_inbound_workflow_activity, workflow_id, start_to_close_timeout=timedelta(seconds=60)
        )


@pytest.mark.asyncio
async def test_the_wait_tells_done_failed_and_not_found_apart(env) -> None:
    """Premortem PM-013: los tests de anterior ausente o fallido pasaban aunque
    la espera reventara (el workflow sigue igual)."""
    steps = Steps()
    steps.broken.add("state:wamid.BAD")
    fakes = [a for a in steps.activities() if a.__temporal_activity_definition.name != "wait_inbound_workflow"]  # type: ignore[attr-defined]
    async with Worker(
        env.client, task_queue=QUEUE, workflows=[InboundMessageWorkflow, _ProbeWait],
        activities=[*fakes, wait_inbound_workflow_activity],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        await (await _start(env, _input("wamid.OK"))).result()
        with pytest.raises(WorkflowFailureError):
            await (await _start(env, _input("wamid.BAD"))).result()

        async def probe(wamid: str) -> str:
            return await env.client.execute_workflow(
                _ProbeWait.run, inbound_workflow_id(wamid), id=f"probe-{wamid}", task_queue=QUEUE
            )

        assert (await probe("wamid.OK"), await probe("wamid.BAD"), await probe("wamid.GHOST")) == (
            "done",
            "failed",
            "not_found",
        )
