"""`InboundMessageWorkflow` con el ingest partido en pasos (entrada durable, fase 2).

Cada paso del ingest es una activity: el estado de la sesión con sus lecturas,
el turno (carrito y cupón), el historial y la entrega; la foto y la nota de voz
tienen el suyo y reentran como texto. Lo que un paso ya hizo queda en el
historial del workflow: un reintento de la entrega no vuelve a leer con Jev ni a
escribir el historial.

Las activities son FAKES registradas por nombre: acá se prueba el orden y lo
que cruza de un paso al otro, no el ingest (ese tiene sus pruebas).
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
from typing import Any

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.chats.agent.sales.contracts import (
    TURN_DELIVER,
    TURN_DESCRIBE,
    TURN_IGNORED,
    TURN_TRANSCRIBE,
    InboundArrival,
    InboundMessageInput,
    IngestRequest,
    IngestState,
    IngestTurn,
    inbound_workflow_id,
)
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.workflows.inbound_message import InboundMessageWorkflow

QUEUE = "queue-sales-agent"
_ARRIVED_MS = 1_789_992_000_000


def _input(wamid: str, *, kind: str = "text", after: str | None = None, received_at_ms: int = _ARRIVED_MS) -> InboundMessageInput:
    message = WhatsAppMessage(
        message_id=wamid, from_number="573001234567", phone_number_id="PID", text=f"texto {wamid}",
        media=None, timestamp="1789992000", msg_type=kind,
    )
    return InboundMessageInput(message=asdict(message), after=after, received_at_ms=received_at_ms)


class Steps:
    """Pasos fakes. `outcome[wamid]` decide qué devuelve el turno; `fail_once`
    hace fallar una vez un paso; `slow[(paso, wamid)]` lo demora."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.outcome: dict[str, str] = {}
        self.fail_once: set[str] = set()
        self.slow: dict[tuple[str, str], float] = {}
        self.broken: set[str] = set()
        self.transcript: str | None = "quiero velas"
        self.states: list[IngestState] = []

    async def _step(self, name: str, request: IngestRequest) -> str:
        wamid = request.message["message_id"]
        self.calls.append(f"{name}:{wamid}")
        await asyncio.sleep(self.slow.get((name, wamid), 0))
        if f"{name}:{wamid}" in self.broken:
            raise ApplicationError("roto", non_retryable=True)
        if f"{name}:{wamid}" in self.fail_once:
            self.fail_once.discard(f"{name}:{wamid}")
            raise ApplicationError("pasajero")
        return wamid

    def activities(self) -> list[Any]:
        steps = self

        @activity.defn(name="register_inbound_arrival")
        async def register(session_id: str, arrival: InboundArrival) -> str:
            steps.calls.append(f"register:{arrival.workflow_id}")
            return "registered"

        @activity.defn(name="read_inbound_state")
        async def read_state(request: IngestRequest) -> IngestState:
            wamid = await steps._step("state", request)
            return IngestState(
                session_id="wa_573001234567", now_ms=1, sent_ms=1, arrival_ms=request.received_at_ms,
                episode_boundary_note=f"nota de {wamid}",
            )

        @activity.defn(name="prepare_inbound_turn")
        async def prepare(request: IngestRequest, state: IngestState) -> IngestTurn:
            wamid = await steps._step("turn", request)
            steps.states.append(state)
            return IngestTurn(outcome=steps.outcome.get(wamid, TURN_DELIVER), text=f"texto {wamid}", turn_message=wamid)

        @activity.defn(name="write_inbound_history")
        async def history(request: IngestRequest, state: IngestState, turn: IngestTurn) -> str:
            await steps._step("history", request)
            return "written"

        @activity.defn(name="deliver_inbound_turn")
        async def deliver(request: IngestRequest, state: IngestState, turn: IngestTurn) -> str:
            await steps._step("deliver", request)
            assert state.episode_boundary_note == f"nota de {request.message['message_id']}"
            return "delivered"

        @activity.defn(name="describe_inbound_image")
        async def describe(request: IngestRequest) -> IngestRequest | None:
            wamid = await steps._step("describe", request)
            message = {**request.message, "message_id": f"{wamid}_vision", "msg_type": "text"}
            return replace(request, message=message, from_photo=True, customer_text_from_message=False)

        @activity.defn(name="transcribe_inbound_audio")
        async def transcribe(request: IngestRequest) -> IngestRequest | None:
            wamid = await steps._step("transcribe", request)
            if steps.transcript is None:
                return None
            message = {**request.message, "message_id": f"{wamid}_transcribed", "text": steps.transcript}
            return replace(request, message=message)

        @activity.defn(name="notify_inbound_photo_done")
        async def photo_done(request: IngestRequest) -> str:
            await steps._step("photo_done", request)
            return "notified"

        @activity.defn(name="wait_inbound_workflow")
        async def wait(workflow_id: str) -> str:
            steps.calls.append(f"wait:{workflow_id}")
            return "done"

        return [register, read_state, prepare, history, deliver, describe, transcribe, photo_done, wait]


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        yield env


def _worker(env: WorkflowEnvironment, steps: Steps) -> Worker:
    return Worker(env.client, task_queue=QUEUE, workflows=[InboundMessageWorkflow], activities=steps.activities())


async def _run(env: WorkflowEnvironment, inp: InboundMessageInput) -> str:
    return await env.client.execute_workflow(
        InboundMessageWorkflow.run, inp, id=inbound_workflow_id(inp.message["message_id"]), task_queue=QUEUE
    )


@pytest.mark.asyncio
async def test_a_text_goes_through_its_steps_in_order(env) -> None:
    steps = Steps()
    async with _worker(env, steps):
        result = await _run(env, _input("wamid.A"))

    assert result == "ingested"
    assert steps.calls == [
        "register:inbound-wamid.A",
        "state:wamid.A",
        "turn:wamid.A",
        "history:wamid.A",
        "deliver:wamid.A",
    ]


@pytest.mark.asyncio
async def test_the_state_reaches_the_later_steps_with_the_arrival_clock(env) -> None:
    steps = Steps()
    async with _worker(env, steps):
        await _run(env, _input("wamid.A", received_at_ms=_ARRIVED_MS + 7))

    assert steps.states[0].arrival_ms == _ARRIVED_MS + 7


@pytest.mark.asyncio
async def test_a_failed_delivery_is_retried_without_repeating_the_earlier_steps(env) -> None:
    """El diario de efectos es el historial del workflow: el reintento de la
    entrega no vuelve a leer con Jev ni a escribir la línea del panel."""
    steps = Steps()
    steps.fail_once.add("deliver:wamid.A")
    async with _worker(env, steps):
        await _run(env, _input("wamid.A"))

    assert steps.calls.count("state:wamid.A") == 1
    assert steps.calls.count("history:wamid.A") == 1
    assert steps.calls.count("deliver:wamid.A") == 2


@pytest.mark.asyncio
async def test_a_photo_reenters_as_text_and_then_tells_the_session_it_is_done(env) -> None:
    steps = Steps()
    steps.outcome["wamid.P"] = TURN_DESCRIBE
    async with _worker(env, steps):
        await _run(env, _input("wamid.P", kind="image"))

    assert steps.calls[1:] == [
        "state:wamid.P",
        "turn:wamid.P",
        "describe:wamid.P",
        "state:wamid.P_vision",
        "turn:wamid.P_vision",
        "history:wamid.P_vision",
        "deliver:wamid.P_vision",
        "photo_done:wamid.P",
    ]


@pytest.mark.asyncio
async def test_a_photo_whose_reading_breaks_fails_in_sight_after_telling_the_session(env) -> None:
    """La foto que no se pudo leer no se traga el error: el workflow queda
    Failed (a la vista y reseteable), igual que cualquier otro paso. Antes de
    fallar le avisa a la sesión que deje de esperarla."""
    steps = Steps()
    steps.outcome["wamid.P"] = TURN_DESCRIBE
    steps.broken.add("describe:wamid.P")
    async with _worker(env, steps):
        with pytest.raises(WorkflowFailureError):
            await _run(env, _input("wamid.P", kind="image"))

    assert steps.calls[-1] == "photo_done:wamid.P"
    assert not any(call.startswith("deliver:") for call in steps.calls)


@pytest.mark.asyncio
async def test_a_voice_note_whose_transcription_breaks_fails_in_sight(env) -> None:
    steps = Steps()
    steps.outcome["wamid.V"] = TURN_TRANSCRIBE
    steps.broken.add("transcribe:wamid.V")
    async with _worker(env, steps):
        with pytest.raises(WorkflowFailureError):
            await _run(env, _input("wamid.V", kind="audio"))

    assert not any(call.startswith("deliver:") for call in steps.calls)


@pytest.mark.asyncio
async def test_a_voice_note_reenters_as_its_transcription(env) -> None:
    steps = Steps()
    steps.outcome["wamid.V"] = TURN_TRANSCRIBE
    async with _worker(env, steps):
        await _run(env, _input("wamid.V", kind="audio"))

    assert steps.calls[-2:] == ["history:wamid.V_transcribed", "deliver:wamid.V_transcribed"]


@pytest.mark.asyncio
async def test_a_voice_note_nobody_understood_stops_after_its_transcription(env) -> None:
    steps = Steps()
    steps.outcome["wamid.V"] = TURN_TRANSCRIBE
    steps.transcript = None
    async with _worker(env, steps):
        assert await _run(env, _input("wamid.V", kind="audio")) == "ingested"

    assert steps.calls[-1] == "transcribe:wamid.V"


@pytest.mark.asyncio
async def test_a_message_without_text_writes_nothing(env) -> None:
    steps = Steps()
    steps.outcome["wamid.R"] = TURN_IGNORED
    async with _worker(env, steps):
        await _run(env, _input("wamid.R", kind="reaction"))

    assert steps.calls[-1] == "turn:wamid.R"


@pytest.mark.asyncio
async def test_a_message_of_the_same_post_reads_after_the_previous_one(env) -> None:
    """Dentro de un POST, el paso del estado espera al anterior (como en la
    fase 1): los dos verían el mismo episodio cerrado o la misma campaña sin
    responder y los dos turnos llevarían la nota de frontera, la campaña y el
    cupón validado dos veces (revisión del PR). Entre POSTs las lecturas sí
    corren en paralelo (`test_inbound_session_order.py`)."""
    steps = Steps()
    async with _worker(env, steps):
        await _run(env, _input("wamid.B", after="wamid.A"))

    assert steps.calls.index("wait:inbound-wamid.A") < steps.calls.index("state:wamid.B")
    assert steps.calls.count("wait:inbound-wamid.A") == 1
