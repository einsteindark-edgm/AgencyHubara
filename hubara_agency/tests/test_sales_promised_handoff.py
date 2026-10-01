"""El bot prometió que un colega lo atiende: el humano queda avisado (V1 y V2).

Laboratorio caso-cortesia-1001 (r1 y r2, 2026-09-30), caso de control: el
cliente pidió que le llevaran el pedido hoy a la portería y los dos bots
contestaron «…un colega del equipo coordina contigo la entrega…» con texto
suelto, sin `escalate_to_human`. La conversación seguía en la ruta del bot y
nadie la veía en la bandeja humana.

Antes de enviar el texto final, el workflow le pregunta a la red de seguridad
(`ensure_promised_handoff_activity`, decisión grabada en la activity) si ese
texto promete el relevo sin que nadie haya escalado. Si escaló: la protección
queda en la traza, el texto sale (la promesa ya es cierta) y el workflow
termina como cualquier escalación. Un turno que ya escaló no se revisa.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.chats.agent.sales.contracts import SalesSessionInput
from src.plugins.chats.agent.sales.workflows.sales_session import HubaraSalesSessionWorkflow
from tests.sales_workflow_versions import sales_workflow_versions
from tests.test_sales_workflow_debounce import (
    _RELAY_FAREWELL,
    SALES_QUEUE,
    Tracker,
    _escalation_batch,
    _escalation_envelope,
    _final_resp,
    _make_fake_activities,
)

V2_EXCLUDED: dict[str, str] = {}
_sales_workflow_version = sales_workflow_versions(__name__, V2_EXCLUDED)

SID = "wa_trace"
ASKED = "Sí, me lo pueden llevar hoy después de las 5 a la portería?"
PROMISE = "Claro que sí 🤍 Un colega del equipo coordina contigo la entrega de hoy después de las 5 en la portería."


async def _run(tracker: Tracker, tmp_path: Path, *, responses, tool_results=None, promised: bool = False) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path=str(workspace),
                llm_responses=responses,
                tool_results=tool_results or {},
                promised_handoff_result=promised,
                prior_history=[
                    {"role": "user", "content": "Hola"},
                    {"role": "assistant", "content": "¡Buenas! Bienvenido a *Hubara*..."},
                ],
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(session_id=SID, runtime_workspace_path=str(workspace)),
                id=f"session-{SID}",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(HubaraSalesSessionWorkflow.send_message, args=[ASKED, None, None])
            await handle.result()


@pytest.mark.asyncio
async def test_a_promise_nobody_escalated_reaches_the_human_inbox(tmp_path: Path) -> None:
    tracker = Tracker()

    await _run(tracker, tmp_path, responses=[_final_resp(PROMISE)], promised=True)

    assert tracker.promised_handoff_calls == [(SID, PROMISE)]
    assert [m for (_s, m) in tracker.send_whatsapp_calls] == [PROMISE], "la promesa sale: ya es cierta"
    assert "safety_net_promised_handoff" in tracker.turn_traces[0]["guards"]
    assert tracker.ghosting_calls == 0, "escaló: el workflow termina sin ciclo de ghosting"


@pytest.mark.asyncio
async def test_a_reply_without_a_promise_is_sent_as_always(tmp_path: Path) -> None:
    tracker = Tracker()
    reply = "Qué alegría que te gustaron 🤍"

    await _run(tracker, tmp_path, responses=[_final_resp(reply)], promised=False)

    assert tracker.promised_handoff_calls == [(SID, reply)]
    assert "safety_net_promised_handoff" not in tracker.turn_traces[0]["guards"]
    assert [m for (_s, m) in tracker.send_whatsapp_calls][0] == reply


@pytest.mark.asyncio
async def test_a_turn_that_escalated_does_not_check_its_farewell(tmp_path: Path) -> None:
    tracker = Tracker()

    await _run(
        tracker,
        tmp_path,
        responses=[_escalation_batch(_RELAY_FAREWELL)],
        tool_results={"escalate_to_human": _escalation_envelope(_RELAY_FAREWELL)},
        promised=True,
    )

    assert tracker.promised_handoff_calls == []
    assert [m for (_s, m) in tracker.send_whatsapp_calls] == [_RELAY_FAREWELL]
