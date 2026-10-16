"""`InboundMessageWorkflow` (ADR 2026-10-15): la entrada durable de un mensaje
del cliente. El webhook lo arranca ANTES de responderle 200 a Meta; el ingest
corre en una activity con reintentos acotados. Un mensaje encadenado al
anterior del mismo cliente (mismo POST) espera a que ese termine: el orden de
hoy, ahora sin depender de un proceso vivo.

El ingest es FAKE (registrado por nombre); la espera del anterior es la real
(le pregunta a Temporal por el workflow de ese mensaje)."""
from __future__ import annotations

import asyncio
from dataclasses import asdict

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.chats.agent.sales.activities.inbound_entry import wait_inbound_predecessor_activity
from src.plugins.chats.agent.sales.contracts import InboundMessageInput, inbound_workflow_id
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.workflows.inbound_message import InboundMessageWorkflow

QUEUE = "queue-sales-agent"


def _input(wamid: str, *, after: str | None = None, text: str = "hola") -> InboundMessageInput:
    message = WhatsAppMessage(
        message_id=wamid, from_number="573001234567", phone_number_id="PID", text=text, media=None, timestamp="1789992000"
    )
    return InboundMessageInput(message=asdict(message), after=after, received_at_ms=1_789_992_000_000)


class _Ingest:
    """Ingest fake: registra inicio/fin por wamid; `slow` tarda; `fail` falla."""

    def __init__(self, *, slow: dict[str, float] | None = None, fail: set[str] | None = None) -> None:
        self.slow = slow or {}
        self.fail = fail or set()
        self.events: list[str] = []
        self.attempts: dict[str, int] = {}

    def activity(self):
        @activity.defn(name="ingest_inbound_message")
        async def fake_ingest(inp: InboundMessageInput) -> str:
            wamid = inp.message["message_id"]
            self.attempts[wamid] = self.attempts.get(wamid, 0) + 1
            if wamid in self.fail:
                raise ApplicationError("vault roto")
            self.events.append(f"{wamid}:start")
            await asyncio.sleep(self.slow.get(wamid, 0))
            self.events.append(f"{wamid}:end")
            return "ingested"

        return fake_ingest


async def _start(env: WorkflowEnvironment, inp: InboundMessageInput):
    return await env.client.start_workflow(
        InboundMessageWorkflow.run, inp, id=inbound_workflow_id(inp.message["message_id"]), task_queue=QUEUE
    )


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        yield env


def _worker(env: WorkflowEnvironment, ingest: _Ingest) -> Worker:
    return Worker(
        env.client,
        task_queue=QUEUE,
        workflows=[InboundMessageWorkflow],
        activities=[ingest.activity(), wait_inbound_predecessor_activity],
    )


@pytest.mark.asyncio
async def test_the_message_is_ingested(env) -> None:
    ingest = _Ingest()
    async with _worker(env, ingest):
        result = await (await _start(env, _input("wamid.A"))).result()

    assert result == "ingested"
    assert ingest.events == ["wamid.A:start", "wamid.A:end"]


@pytest.mark.asyncio
async def test_a_message_waits_for_the_previous_one_of_the_same_customer(env) -> None:
    ingest = _Ingest(slow={"wamid.A": 1.0})
    async with _worker(env, ingest):
        first = await _start(env, _input("wamid.A"))
        second = await _start(env, _input("wamid.B", after="wamid.A"))
        await asyncio.gather(first.result(), second.result())

    assert ingest.events == ["wamid.A:start", "wamid.A:end", "wamid.B:start", "wamid.B:end"]


@pytest.mark.asyncio
async def test_a_missing_previous_message_does_not_hold_the_next(env) -> None:
    ingest = _Ingest()
    async with _worker(env, ingest):
        result = await (await _start(env, _input("wamid.B", after="wamid.GHOST"))).result()

    assert result == "ingested"


@pytest.mark.asyncio
async def test_a_previous_message_that_failed_does_not_hold_the_next(env) -> None:
    ingest = _Ingest(fail={"wamid.A"})
    async with _worker(env, ingest):
        first = await _start(env, _input("wamid.A"))
        second = await _start(env, _input("wamid.B", after="wamid.A"))
        with pytest.raises(WorkflowFailureError):
            await first.result()
        assert await second.result() == "ingested"


@pytest.mark.asyncio
async def test_an_ingest_that_keeps_failing_gives_up_after_five_attempts_spread_over_minutes(env) -> None:
    """Acotado, pero no de segundos: un vault o un Temporal que tarda un par de
    minutos en volver no debe dejar el mensaje en Failed (Meta ya tiene su 200
    y no lo re-entrega). El diario de efectos hace seguro reintentar."""
    ingest = _Ingest(fail={"wamid.A"})
    async with _worker(env, ingest):
        handle = await _start(env, _input("wamid.A"))
        with pytest.raises(WorkflowFailureError):
            await handle.result()
        history = await handle.fetch_history()

    assert ingest.attempts["wamid.A"] == 5
    elapsed = history.events[-1].event_time.ToSeconds() - history.events[0].event_time.ToSeconds()
    assert elapsed >= 120


@pytest.mark.asyncio
async def test_a_stuck_previous_message_holds_the_next_only_up_to_the_cap(env, monkeypatch) -> None:
    """El orden no vale un mensaje perdido: si el anterior se traba, el
    siguiente sale al vencer el tope de la espera (acá 0,5 s; en prod 2 min)."""
    from src.plugins.chats.agent.sales.activities import inbound_entry

    monkeypatch.setattr(inbound_entry, "PREDECESSOR_WAIT_S", 0.5)
    release = asyncio.Event()

    @activity.defn(name="ingest_inbound_message")
    async def stuck_first(inp: InboundMessageInput) -> str:
        if inp.message["message_id"] == "wamid.A":
            await release.wait()
        return "ingested"

    async with Worker(
        env.client, task_queue=QUEUE, workflows=[InboundMessageWorkflow],
        activities=[stuck_first, wait_inbound_predecessor_activity],
    ):
        first = await _start(env, _input("wamid.A"))
        second = await _start(env, _input("wamid.B", after="wamid.A"))

        assert await asyncio.wait_for(second.result(), timeout=30) == "ingested"
        release.set()
        assert await first.result() == "ingested"
