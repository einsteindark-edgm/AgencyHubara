"""`InboundEntry` (ADR 2026-10-15 §1): lo que hace el webhook ANTES del 200 —
dejar el mensaje en Temporal como `inbound-<wamid>`. Una re-entrega de Meta
choca con el id y no entra dos veces; si Temporal no contesta, la falla sube
(el webhook responde 503 y Meta reintenta) y la próxima vez se reconecta."""
from __future__ import annotations

from dataclasses import asdict

import pytest
from temporalio.client import WorkflowExecutionStatus
from temporalio.testing import WorkflowEnvironment

from src.plugins.chats.agent.sales.contracts import InboundMessageInput
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.inbound_entry import InboundEntry

QUEUE = "queue-sales-agent"


def _input(wamid: str) -> InboundMessageInput:
    message = WhatsAppMessage(
        message_id=wamid, from_number="573001234567", phone_number_id="PID", text="hola", media=None, timestamp="1789992000"
    )
    return InboundMessageInput(message=asdict(message))


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        yield env


@pytest.mark.asyncio
async def test_the_message_is_left_in_temporal_under_its_wamid(env) -> None:
    async def factory():
        return env.client

    accepted = await InboundEntry(factory, task_queue=QUEUE).accept(_input("wamid.A"))

    description = await env.client.get_workflow_handle("inbound-wamid.A").describe()
    assert accepted is True
    assert description.workflow_type == "InboundMessageWorkflow"
    assert description.task_queue == QUEUE
    assert description.status == WorkflowExecutionStatus.RUNNING


@pytest.mark.asyncio
async def test_a_message_meta_delivers_again_is_not_started_twice(env) -> None:
    async def factory():
        return env.client

    entry = InboundEntry(factory, task_queue=QUEUE)
    await entry.accept(_input("wamid.A"))
    await env.client.get_workflow_handle("inbound-wamid.A").terminate("ya se ingirió")

    assert await entry.accept(_input("wamid.A")) is False


@pytest.mark.asyncio
async def test_the_client_is_reused_and_rebuilt_after_a_failure(env) -> None:
    calls: list[int] = []

    async def factory():
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("temporal cloud no contesta")
        return env.client

    entry = InboundEntry(factory, task_queue=QUEUE)
    with pytest.raises(ConnectionError):
        await entry.accept(_input("wamid.A"))
    await entry.accept(_input("wamid.B"))
    await entry.accept(_input("wamid.C"))

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_messages_without_wamid_do_not_collide(env) -> None:
    """El parser exige un `id` de texto, pero no que tenga algo: sin wamid no
    hay dedupe posible, y dos mensajes así no pueden pisarse en `inbound-`."""
    async def factory():
        return env.client

    entry = InboundEntry(factory, task_queue=QUEUE)

    assert await entry.accept(_input("")) is True
    assert await entry.accept(_input("")) is True
