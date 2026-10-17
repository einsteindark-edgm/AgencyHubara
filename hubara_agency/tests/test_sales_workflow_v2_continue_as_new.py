"""El continue-as-new del bot nuevo no vuelve a contestar lo que ya entró con
un traspaso (premortem de la Ola 0 de Temporal, 2026-10-16).

Desde el traspaso con lo que escribió el cliente (caso 2026-10-16), el V2
recuerda los wamids que ya entraron con el traspaso (`_handoff_wamids`): si el
mismo mensaje también llega por señal, no se contesta dos veces. Ese recuerdo
vivía solo en el run; un continue-as-new lo perdía y una señal tardía del
mismo mensaje (el ingest durable reintenta) recibía una segunda respuesta.
Ahora viaja en el input del run siguiente, como los asuntos pendientes.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from exoclaw_temporal.config import LLMConfig, SessionInput, WorkspaceConfig
from src.plugins.chats.agent.sales.contracts import HandoffReply, SalesSessionInput
from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2
from tests.test_sales_workflow_debounce import SALES_QUEUE, Tracker, _final_resp, _make_fake_activities

pytestmark = pytest.mark.asyncio

_SUMMARY = 'Cliente respondió "Si" al gancho de reactivación. Siguiente paso: datos de envío.'
_REPLY = HandoffReply(text="Si", customer_text="Si", wamids=["wamid.in9"], ts_ms=1792184941000)
_ANSWER = "¡Listo! Para enviarte tu caja necesito unos datos 🤍"


def _activities(tmp_path: Path, tracker: Tracker, bootstraps: list[SalesSessionInput], **fakes) -> list:
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)

    @activity.defn(name="bootstrap_sales_session_activity")
    async def recording_bootstrap(input: SalesSessionInput) -> SessionInput:
        bootstraps.append(input)
        return SessionInput(
            session_id=input.session_id,
            channel="whatsapp",
            chat_id=input.session_id,
            llm=LLMConfig(model="fake"),
            workspace=WorkspaceConfig(path=str(workspace)),
            tool_definitions_json="[]",
        )

    base = _make_fake_activities(tracker, workspace_path=str(workspace), **fakes)
    return [a for a in base if a.__temporal_activity_definition.name != "bootstrap_sales_session_activity"] + [
        recording_bootstrap
    ]


async def test_the_handoff_wamids_travel_to_the_next_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # El servidor sugiere cortar tras el turno del traspaso.
    monkeypatch.setattr(workflow.Info, "is_continue_as_new_suggested", lambda self: True)
    tracker, bootstraps = Tracker(), []
    acts = _activities(
        tmp_path,
        tracker,
        bootstraps,
        handoff_sequence=[_SUMMARY],
        handoff_reply=_REPLY,
        llm_responses=[_final_resp(_ANSWER)],
    )
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue=SALES_QUEUE, workflows=[HubaraSalesSessionWorkflowV2], activities=acts):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflowV2.run,
                SalesSessionInput(session_id="wa_can_handoff"),
                id="session-wa_can_handoff",
                task_queue=SALES_QUEUE,
            )
            await handle.result()

    assert [m for (_s, m) in tracker.send_whatsapp_calls] == [_ANSWER]
    assert len(bootstraps) == 2, f"esperaba el corte sugerido tras el turno del traspaso: {len(bootstraps)}"
    assert bootstraps[1].handoff_wamids == ["wamid.in9"]


async def test_a_late_signal_of_a_message_that_came_with_the_handoff_is_not_answered_again(tmp_path: Path) -> None:
    tracker, bootstraps = Tracker(), []
    acts = _activities(tmp_path, tracker, bootstraps, llm_responses=[_final_resp(_ANSWER)])
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue=SALES_QUEUE, workflows=[HubaraSalesSessionWorkflowV2], activities=acts):
            # El run que dejó el continue-as-new: el «Si» ya entró con el traspaso.
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflowV2.run,
                SalesSessionInput(session_id="wa_can_handoff", handoff_wamids=["wamid.in9"]),
                id="session-wa_can_handoff",
                task_queue=SALES_QUEUE,
            )
            meta = {"wamid": "wamid.in9", "ts_ms": 1792184941000, "kind": "text", "text": "Si"}
            await handle.signal(HubaraSalesSessionWorkflowV2.send_message, args=["Si", None, None, meta])
            await handle.result()

    assert [c.message for c in tracker.build_prompt_calls if "GHOST" not in c.message] == []
    assert tracker.send_whatsapp_calls == []


async def test_the_note_of_an_unconfirmed_reply_travels_to_the_next_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si el run corta justo después de una respuesta que no se confirmó, el
    aviso para el turno siguiente viaja en el input del run nuevo."""
    from temporalio.exceptions import ApplicationError

    monkeypatch.setattr(workflow.Info, "is_continue_as_new_suggested", lambda self: True)
    tracker, bootstraps = Tracker(), []

    @activity.defn(name="send_whatsapp_message_activity")
    async def failing_send(session_id: str, message: str, decided_by_engine: bool = False) -> list[dict] | None:
        raise ApplicationError("Activity task timed out", non_retryable=True)

    acts = [
        a
        for a in _activities(
            tmp_path,
            tracker,
            bootstraps,
            llm_responses=[_final_resp(_ANSWER)],
            prior_history=[{"role": "user", "content": "Hola"}, {"role": "assistant", "content": "¡Buenas!"}],
        )
        if a.__temporal_activity_definition.name != "send_whatsapp_message_activity"
    ] + [failing_send]
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue=SALES_QUEUE, workflows=[HubaraSalesSessionWorkflowV2], activities=acts):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflowV2.run,
                SalesSessionInput(session_id="wa_can_undelivered"),
                id="session-wa_can_undelivered",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(HubaraSalesSessionWorkflowV2.send_message, args=["Quiero una caja", None, None])
            await handle.result()

    assert len(bootstraps) == 2, f"esperaba el corte sugerido tras el turno: {len(bootstraps)}"
    carried = bootstraps[1].undelivered or []
    assert len(carried) == 1 and "pudo no llegarle" in carried[0] and _ANSWER in carried[0], carried
