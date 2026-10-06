"""RemarketingWorkflow con la decisión `contactar` del motor (fase F8).

Si el contexto del gancho dice que el toque sobra (`skip_touch`, decidido por
Jev ANTES de redactar), el workflow no llama al LLM ni muestra «escribiendo»:
toma el mismo camino de una abstención (consume el peldaño, devuelve el
routing a Ventas y termina). Sin la decisión (el bot de hoy, o historias
viejas sin el campo), el LLM decide como siempre.
"""
from __future__ import annotations

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.chats.agent.remarketing.contracts import RemarketingContext
from src.plugins.chats.agent.remarketing.workflows.remarketing import RemarketingSessionWorkflow
from tests.plugins.chats.test_remarketing_ladder_workflow import (
    REMARKETING_QUEUE,
    SID,
    Tracker,
    _fakes,
    _input,
    _wait_for,
)


def _with_context(activities: list, tracker: Tracker, *, skip_on_touch: set[int]) -> list:
    @activity.defn(name="read_remarketing_context_activity")
    async def fake_context(session_id: str) -> RemarketingContext:
        touch = len(tracker.touches) + 1
        return RemarketingContext(touch_number=touch, silence_minutes=125, skip_touch=touch in skip_on_touch)

    return [a for a in activities if getattr(a, "__name__", "") != "fake_context"] + [fake_context]


@pytest.mark.asyncio
async def test_a_touch_the_engine_says_is_not_needed_is_not_drafted(tmp_path) -> None:
    tracker = Tracker()
    acts = _with_context(_fakes(tracker, llm_contents=["no debería redactarse"], workspace_path=str(tmp_path)),
                         tracker, skip_on_touch={1})
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue=REMARKETING_QUEUE, workflows=[RemarketingSessionWorkflow], activities=acts):
            handle = await env.client.start_workflow(
                RemarketingSessionWorkflow.run, _input(), id=f"remarketing-{SID}", task_queue=REMARKETING_QUEUE,
            )
            await handle.result()
    assert tracker.llm_calls == 0 and tracker.sends == []
    assert tracker.touches == ["abstained"], "el peldaño se consume igual que una abstención"
    assert tracker.claim_calls == ["remarketing", "ventas"]


@pytest.mark.asyncio
async def test_the_next_touch_can_also_be_skipped_without_the_llm(tmp_path) -> None:
    tracker = Tracker()
    acts = _with_context(_fakes(tracker, llm_contents=["Primer gancho 🌿", "no debería redactarse"],
                                workspace_path=str(tmp_path)), tracker, skip_on_touch={2})
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue=REMARKETING_QUEUE, workflows=[RemarketingSessionWorkflow], activities=acts):
            handle = await env.client.start_workflow(
                RemarketingSessionWorkflow.run, _input(), id=f"remarketing-{SID}", task_queue=REMARKETING_QUEUE,
            )
            await _wait_for(lambda: len(tracker.touches) == 1)
            await handle.signal("next_touch", {"session_id": SID, "motivo": "ws"})
            await handle.result()
    assert tracker.sends == ["Primer gancho 🌿"] and tracker.llm_calls == 1
    assert tracker.touches == ["free_form", "abstained"]
    assert tracker.claim_calls[-1] == "ventas"
