"""Un envío que falla del todo no tumba la sesión del bot nuevo (auditoría de
Temporal, 2026-10-15).

En el V2, si `send_whatsapp_message_activity` (saludo o texto del turno),
`flush_pending_ui_intents_activity` (catálogo, selector, formulario) o el aviso
al dispatcher del cierre (`dispatch_event`) agotaban sus intentos, la excepción
subía hasta el `run`: el workflow FALLABA, la sesión perdía su estado y el
siguiente mensaje del cliente arrancaba otra de cero. Una falla técnica se
recupera sola y avisa (log), sin pasar al equipo: la sesión sigue.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.chats.agent.sales.contracts import SalesSessionInput
from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2
from tests.plugins.chats.test_sales_capi_trigger import (
    _closing_turn_responses,
    _episode_closed_tool_result,
    _make_capi_and_dispatch_fakes,
)
from tests.test_sales_workflow_debounce import SALES_QUEUE, Tracker, _make_fake_activities

SESSION = "wa_v2_delivery"


@activity.defn(name="send_whatsapp_message_activity")
async def _send_down(session_id: str, message: str, decided_by_engine: bool = False) -> list[dict] | None:
    raise ApplicationError("graph.facebook.com 500", non_retryable=True)


@activity.defn(name="flush_pending_ui_intents_activity")
async def _flush_down(session_id: str) -> list[dict]:
    raise ApplicationError("graph.facebook.com 500", non_retryable=True)


@activity.defn(name="orchestration.dispatch_event")
async def _dispatch_down(envelope) -> dict:
    raise ApplicationError("dispatcher caído", non_retryable=True)


async def _session(tmp_path: Path, *, broken: list, closing: bool = False) -> tuple[Tracker, list[dict]]:
    tracker = Tracker()
    capi_calls: list[dict] = []
    workspace = tmp_path / "ws"
    workspace.mkdir()
    fakes = _make_fake_activities(
        tracker,
        workspace_path=str(workspace),
        **(
            {
                "llm_responses": _closing_turn_responses("COMPRA_EXITOSA"),
                "tool_results": {"manage_conversation_tag": _episode_closed_tool_result(SESSION, "COMPRA_EXITOSA")},
            }
            if closing
            else {}
        ),
    ) + _make_capi_and_dispatch_fakes(capi_calls)
    names = {a.__temporal_activity_definition.name for a in broken}
    activities = [a for a in fakes if a.__temporal_activity_definition.name not in names] + broken

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue=SALES_QUEUE, workflows=[HubaraSalesSessionWorkflowV2], activities=activities):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflowV2.run,
                SalesSessionInput(session_id=SESSION, runtime_workspace_path=str(workspace)),
                id=f"session-{SESSION}",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(HubaraSalesSessionWorkflowV2.send_message, args=["Hola, ¿tienen velas de lavanda?", None, None])
            try:
                await handle.result()
            except WorkflowFailureError as exc:
                pytest.fail(f"la sesión murió por una falla de entrega: {exc.cause!r}")
    return tracker, capi_calls


def _ghost_turn_ran(tracker: Tracker) -> bool:
    return any("GHOST" in c.message for c in tracker.build_prompt_calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", [_send_down, _flush_down], ids=["send_whatsapp", "flush_ui_intents"])
async def test_a_send_that_fails_for_good_leaves_the_session_alive(tmp_path: Path, broken) -> None:
    tracker, _ = await _session(tmp_path, broken=[broken])
    assert _ghost_turn_ran(tracker), "la sesión no siguió después de la falla"


@pytest.mark.asyncio
async def test_the_close_still_reaches_meta_when_the_dispatcher_is_down(tmp_path: Path) -> None:
    _, capi_calls = await _session(tmp_path, broken=[_dispatch_down], closing=True)
    assert [c["event_name"] for c in capi_calls] == ["Purchase"]
