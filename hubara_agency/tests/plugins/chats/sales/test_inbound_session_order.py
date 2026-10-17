"""El orden de entrega por cliente (entrada durable, fase 2, pasos 3 y 4;
ADR 2026-10-16).

Cada mensaje se anuncia al `InboundSessionWorkflow` de su cliente
(`inbound-wa_<número>`), que le responde con su anterior: el último anunciado
con una llegada al webhook menor. El mensaje lee (Jev, el carrito, la foto) en
seguida, en paralelo con los demás, y antes de escribir el historial espera, con
tope, a que termine el workflow de su anterior. Así:

* la ráfaga entera llega junta a la sesión y en el orden en que entró, aunque
  la lectura del primero tarde más (antes, POSTs distintos se entregaban en el
  orden en que terminaban);
* el texto escrito detrás de una foto espera a que la foto entre al bot (lo
  que hacía `PhotoReads` en memoria del proceso, ahora durable y sin el
  invariante de una sola réplica del worker `sales`).

El anuncio, la respuesta y la espera son reales (contra el Temporal de prueba);
los pasos del ingest son fakes (`Steps`).
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from temporalio.client import WorkflowExecutionStatus
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.chats.agent.sales.activities import inbound_entry
from src.plugins.chats.agent.sales.activities.inbound_entry import (
    register_inbound_arrival_activity,
    wait_inbound_workflow_activity,
)
from src.plugins.chats.agent.sales.contracts import (
    TURN_DESCRIBE,
    InboundArrival,
    InboundMessageInput,
    inbound_session_workflow_id,
    inbound_workflow_id,
)
from src.plugins.chats.agent.sales.workflows.inbound_message import InboundMessageWorkflow
from src.plugins.chats.agent.sales.workflows.inbound_session import InboundSessionWorkflow, predecessor_of
from tests.plugins.chats.sales.test_inbound_message_steps_workflow import QUEUE, Steps, _input

_SESSION = "wa_573001234567"


# --- Quién es el anterior (función pura) -------------------------------------


def _arrival(workflow_id: str, at_ms: int) -> InboundArrival:
    return InboundArrival(workflow_id=workflow_id, received_at_ms=at_ms)


def test_the_first_message_of_a_customer_has_no_previous() -> None:
    assert predecessor_of([], _arrival("inbound-A", 1_000)) is None


def test_the_previous_is_the_last_one_that_arrived_before() -> None:
    recent = [_arrival("inbound-A", 1_000), _arrival("inbound-B", 2_000), _arrival("inbound-C", 3_000)]

    assert predecessor_of(recent, _arrival("inbound-D", 4_000)) == "inbound-C"


def test_an_announcement_that_arrives_late_still_gets_the_one_before_it() -> None:
    """Los anuncios pueden llegar desordenados (dos activities a la vez): manda
    la hora de llegada al webhook, no el orden del anuncio."""
    recent = [_arrival("inbound-A", 1_000), _arrival("inbound-C", 3_000)]

    assert predecessor_of(recent, _arrival("inbound-B", 2_000)) == "inbound-A"


def test_a_message_that_arrived_in_the_same_millisecond_is_not_its_previous() -> None:
    """Estrictamente antes: dos mensajes del mismo instante no se esperan entre
    sí (dentro de un POST los ordena `after`). Así nunca hay un ciclo."""
    recent = [_arrival("inbound-A", 1_000), _arrival("inbound-B", 2_000)]

    assert predecessor_of(recent, _arrival("inbound-C", 2_000)) == "inbound-A"
    assert predecessor_of(recent, _arrival("inbound-B", 2_000)) == "inbound-A"


# --- Con Temporal ------------------------------------------------------------


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        yield env


def _worker(env: WorkflowEnvironment, steps: Steps, **kwargs: Any) -> Worker:
    fakes = [a for a in steps.activities() if a.__temporal_activity_definition.name not in {  # type: ignore[attr-defined]
        "register_inbound_arrival", "wait_inbound_workflow",
    }]
    return Worker(
        env.client,
        task_queue=QUEUE,
        workflows=[InboundMessageWorkflow, InboundSessionWorkflow],
        activities=[*fakes, register_inbound_arrival_activity, wait_inbound_workflow_activity],
        **kwargs,
    )


async def _start(env: WorkflowEnvironment, inp: InboundMessageInput):
    return await env.client.start_workflow(
        InboundMessageWorkflow.run, inp, id=inbound_workflow_id(inp.message["message_id"]), task_queue=QUEUE
    )


@pytest.mark.asyncio
async def test_messages_from_different_posts_reach_the_session_in_arrival_order(env) -> None:
    """«hola» tarda en leerse (Jev); «quiero velas» llega 1 s después y se lee
    rápido. Se leen a la vez, pero «hola» entra primero."""
    steps = Steps()
    steps.slow[("turn", "wamid.A")] = 1.5
    async with _worker(env, steps):
        first = await _start(env, _input("wamid.A", received_at_ms=1_000))
        await asyncio.sleep(0.2)
        second = await _start(env, _input("wamid.B", received_at_ms=2_000))
        await asyncio.wait_for(asyncio.gather(first.result(), second.result()), timeout=30)

    assert steps.calls.index("turn:wamid.B") < steps.calls.index("deliver:wamid.A")  # leyó sin esperar
    assert steps.calls.index("deliver:wamid.A") < steps.calls.index("history:wamid.B")


@pytest.mark.asyncio
async def test_a_text_written_after_a_photo_waits_for_the_photo(env) -> None:
    steps = Steps()
    steps.outcome["wamid.P"] = TURN_DESCRIBE
    steps.slow[("describe", "wamid.P")] = 1.5
    async with _worker(env, steps):
        photo = await _start(env, _input("wamid.P", kind="image", received_at_ms=1_000))
        await asyncio.sleep(0.2)
        text = await _start(env, _input("wamid.T", received_at_ms=2_000))
        await asyncio.wait_for(asyncio.gather(photo.result(), text.result()), timeout=30)

    assert steps.calls.index("deliver:wamid.P_vision") < steps.calls.index("history:wamid.T")
    assert steps.calls.index("photo_done:wamid.P") < steps.calls.index("history:wamid.T")


@pytest.mark.asyncio
async def test_a_stuck_previous_message_holds_the_next_only_up_to_the_cap(env, monkeypatch) -> None:
    """El orden no vale un mensaje perdido: si el anterior se traba, el
    siguiente sale al vencer el tope (acá 0,5 s; en prod 30 s)."""
    monkeypatch.setattr(inbound_entry, "PREDECESSOR_GATE_S", 0.5)
    steps = Steps()
    steps.slow[("deliver", "wamid.A")] = 3_600
    async with _worker(env, steps):
        await _start(env, _input("wamid.A", received_at_ms=1_000))
        await asyncio.sleep(0.2)
        second = await _start(env, _input("wamid.B", received_at_ms=2_000))

        assert await asyncio.wait_for(second.result(), timeout=30) == "ingested"
        assert "deliver:wamid.B" in steps.calls


@pytest.mark.asyncio
async def test_the_customer_workflow_ends_when_nobody_writes(env) -> None:
    steps = Steps()
    async with _worker(env, steps):
        await (await _start(env, _input("wamid.A"))).result()
        handle = env.client.get_workflow_handle(inbound_session_workflow_id(_SESSION))
        await env.sleep(11 * 60)
        await asyncio.wait_for(handle.result(), timeout=30)

        assert (await handle.describe()).status == WorkflowExecutionStatus.COMPLETED



@pytest.mark.asyncio
async def test_the_customer_workflow_remembers_who_arrived_across_continue_as_new(env, monkeypatch) -> None:
    """Tras el continue-as-new (acá cada 2 respuestas), el siguiente mensaje
    sigue teniendo su anterior: lo anunciado viaja al run nuevo. Sin sandbox,
    para poder bajar el umbral en el test."""
    from temporalio.worker import UnsandboxedWorkflowRunner

    from src.plugins.chats.agent.sales.workflows import inbound_session

    monkeypatch.setattr(inbound_session, "_CONTINUE_AS_NEW_AFTER", 2)
    steps = Steps()
    steps.slow[("turn", "wamid.B")] = 1.0
    session = env.client.get_workflow_handle(inbound_session_workflow_id(_SESSION))
    async with _worker(env, steps, workflow_runner=UnsandboxedWorkflowRunner()):
        first = await _start(env, _input("wamid.A", received_at_ms=1_000))
        await asyncio.sleep(0.3)
        first_run = (await session.describe()).run_id
        second = await _start(env, _input("wamid.B", received_at_ms=2_000))
        await asyncio.sleep(0.3)
        third = await _start(env, _input("wamid.C", received_at_ms=3_000))
        await asyncio.wait_for(asyncio.gather(first.result(), second.result(), third.result()), timeout=30)
        run = await env.client.get_workflow_handle(session.id, run_id=first_run).describe()

    assert run.status == WorkflowExecutionStatus.CONTINUED_AS_NEW
    assert steps.calls.index("deliver:wamid.B") < steps.calls.index("history:wamid.C")
