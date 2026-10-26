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


@activity.defn(name="persist_assistant_message_activity")
async def _panel_down(session_id: str, message: str, tools_used=None, delivered=None) -> None:
    raise ApplicationError("metadata.json dañado", non_retryable=True)


@pytest.mark.asyncio
async def test_a_panel_write_that_fails_after_the_send_leaves_the_session_alive(tmp_path: Path) -> None:
    """El mensaje ya salió: anotarlo en el panel es de mejor esfuerzo (lo que
    registra algo que ya salió nunca tumba ni reenvía)."""
    tracker, _ = await _session(tmp_path, broken=[_panel_down])
    assert tracker.send_whatsapp_calls, "la respuesta no salió"
    assert _ghost_turn_ran(tracker), "la sesión murió porque el panel no se pudo escribir"


@pytest.mark.asyncio
async def test_the_close_still_reaches_meta_when_the_dispatcher_is_down(tmp_path: Path) -> None:
    _, capi_calls = await _session(tmp_path, broken=[_dispatch_down], closing=True)
    assert [c["event_name"] for c in capi_calls] == ["Purchase"]


@pytest.mark.asyncio
async def test_a_cancel_requested_during_a_send_still_cancels_the_session(tmp_path: Path) -> None:
    """Una cancelación (un operador desde Temporal) mientras sale un envío no es
    una falla de entrega: la sesión se cancela, no sigue como si nada."""
    import asyncio

    from temporalio.exceptions import CancelledError

    sending = asyncio.Event()

    @activity.defn(name="send_whatsapp_message_activity")
    async def slow_send(session_id: str, message: str, decided_by_engine: bool = False) -> list[dict] | None:
        sending.set()
        while True:  # late para que la cancelación le llegue
            activity.heartbeat()
            await asyncio.sleep(0.05)

    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    fakes = _make_fake_activities(tracker, workspace_path=str(workspace))
    activities = [a for a in fakes if a.__temporal_activity_definition.name != "send_whatsapp_message_activity"] + [
        slow_send
    ]
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue=SALES_QUEUE, workflows=[HubaraSalesSessionWorkflowV2], activities=activities):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflowV2.run,
                SalesSessionInput(session_id=SESSION, runtime_workspace_path=str(workspace)),
                id=f"session-{SESSION}",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(HubaraSalesSessionWorkflowV2.send_message, args=["Hola", None, None])
            await asyncio.wait_for(sending.wait(), timeout=30)
            await handle.cancel()
            with pytest.raises(WorkflowFailureError) as failure:
                await handle.result()

    assert isinstance(failure.value.cause, CancelledError), f"la sesión no se canceló: {failure.value.cause!r}"
    assert not any("GHOST" in c.message for c in tracker.build_prompt_calls), "la sesión siguió tras la cancelación"


_PRIOR = [{"role": "user", "content": "Hola"}, {"role": "assistant", "content": "¡Buenas!"}]
_REPLY = "¡Claro! Tenemos la vela de lavanda 🤍"


async def _run_v2(tmp_path: Path, *, send, llm_responses, prior_history=_PRIOR, scenario=None) -> Tracker:
    """Una sesión del V2 con un `send(handle, message)` propio para los textos:
    devuelve las burbujas que salieron o lanza. `scenario(env, handle)` corre
    después del primer mensaje del cliente."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    box: dict = {}

    @activity.defn(name="send_whatsapp_message_activity")
    async def custom_send(session_id: str, message: str, decided_by_engine: bool = False) -> list[dict] | None:
        return await send(box["handle"], message, tracker)

    fakes = _make_fake_activities(
        tracker, workspace_path=str(workspace), llm_responses=llm_responses, prior_history=prior_history
    )
    activities = [a for a in fakes if a.__temporal_activity_definition.name != "send_whatsapp_message_activity"] + [
        custom_send
    ]
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue=SALES_QUEUE, workflows=[HubaraSalesSessionWorkflowV2], activities=activities):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflowV2.run,
                SalesSessionInput(session_id=SESSION, runtime_workspace_path=str(workspace)),
                id=f"session-{SESSION}",
                task_queue=SALES_QUEUE,
            )
            box["handle"] = handle
            await handle.signal(HubaraSalesSessionWorkflowV2.send_message, args=["Hola, ¿tienen velas de lavanda?", None, None])
            if scenario is not None:
                await scenario(env, handle)
            await handle.result()
    return tracker


def _delivered(tracker: Tracker, message: str) -> list[dict]:
    tracker.send_whatsapp_calls.append((SESSION, message))
    return [{"wamid": f"wamid.out{len(tracker.send_whatsapp_calls)}", "text": message}]


def _outbound_bubbles(tracker: Tracker, needle: str) -> list[dict]:
    return [
        bubble
        for trace in tracker.turn_traces
        for step in trace.get("steps") or []
        if step.get("kind") == "outbound"
        for bubble in step.get("bubbles") or []
        if needle in str(bubble.get("text", ""))
    ]


def _second_turn_context(tracker: Tracker) -> str:
    prompts = [c for c in tracker.build_prompt_calls if "GHOST" not in c.message]
    assert len(prompts) == 2, [c.message for c in prompts]
    return "\n".join(prompts[1].plugin_context or [])


@pytest.mark.asyncio
async def test_a_reply_whose_send_failed_shows_in_the_panel_without_confirmation(tmp_path: Path) -> None:
    """El envío que falla del todo es AMBIGUO (lo que lanza es un timeout, un
    worker que se apagó o el vault después del POST: Meta pudo entregarlo). Se
    registra como enviado «sin confirmación»: el panel lo muestra sin doble
    check y la traza no dice que una protección se llevó el texto."""
    from tests.test_sales_workflow_debounce import _final_resp

    async def send(handle, message, tracker):
        if "lavanda" in message:
            raise ApplicationError("Activity task timed out", non_retryable=True)
        return _delivered(tracker, message)

    tracker = await _run_v2(tmp_path, send=send, llm_responses=[_final_resp(_REPLY)])

    persisted = [w for (_s, m), w in zip(tracker.persist_calls, tracker.persisted_wamids) if "lavanda" in m]
    assert persisted == [None], f"el panel no muestra la respuesta sin confirmación (wamids): {persisted}"
    assert [b.get("delivered") for b in _outbound_bubbles(tracker, "lavanda")] == [None]
    guards = [s for t in tracker.turn_traces for s in t.get("steps") or [] if s.get("kind") == "guard"]
    assert not any(g.get("name") == "delivery_failed" for g in guards)


@pytest.mark.asyncio
async def test_the_next_turn_knows_its_previous_reply_may_not_have_arrived(tmp_path: Path) -> None:
    """El historial del modelo graba la respuesta ANTES de enviarla: el turno
    siguiente le avisa que quizá no llegó (si no, da por dicho lo que el
    cliente pudo no ver). Cuando un envío vuelve a salir, el aviso se va."""
    import asyncio

    from tests.test_sales_workflow_debounce import _final_resp

    failed = asyncio.Event()

    async def send(handle, message, tracker):
        if "lavanda" in message:
            failed.set()
            raise ApplicationError("Activity task timed out", non_retryable=True)
        return _delivered(tracker, message)

    async def customer_writes_again(env, handle) -> None:
        await asyncio.wait_for(failed.wait(), timeout=60)
        await env.sleep(5)
        await handle.signal(HubaraSalesSessionWorkflowV2.send_message, args=["¿hola?", None, None])

    tracker = await _run_v2(
        tmp_path,
        send=send,
        llm_responses=[_final_resp(_REPLY), _final_resp("Sí, hacemos envíos a todo el país 🤍")],
        scenario=customer_writes_again,
    )

    second = _second_turn_context(tracker)
    assert "pudo no llegarle" in second and "lavanda" in second, second
    ghost = [c for c in tracker.build_prompt_calls if "GHOST" in c.message]
    assert ghost and not any("llegarle" in n for n in ghost[0].plugin_context or []), "el aviso siguió tras un envío que salió"


@pytest.mark.asyncio
async def test_the_next_turn_knows_whatsapp_rejected_its_previous_reply(tmp_path: Path) -> None:
    """Un rechazo de WhatsApp no lanza: la activity devuelve que no salió
    ninguna burbuja. Eso sí es definitivo: el turno siguiente lo sabe."""
    import asyncio

    from tests.test_sales_workflow_debounce import _final_resp

    rejected = asyncio.Event()

    async def send(handle, message, tracker):
        if "lavanda" in message:
            rejected.set()
            return []
        return _delivered(tracker, message)

    async def customer_writes_again(env, handle) -> None:
        await asyncio.wait_for(rejected.wait(), timeout=60)
        await env.sleep(5)
        await handle.signal(HubaraSalesSessionWorkflowV2.send_message, args=["¿hola?", None, None])

    tracker = await _run_v2(
        tmp_path,
        send=send,
        llm_responses=[_final_resp(_REPLY), _final_resp("Sí, a todo el país 🤍")],
        scenario=customer_writes_again,
    )

    second = _second_turn_context(tracker)
    assert "no le llegó" in second and "lavanda" in second, second


@pytest.mark.asyncio
async def test_a_message_written_while_a_rejected_greeting_was_sent_came_before_the_reply(tmp_path: Path) -> None:
    """La primera salida del turno es la que SALE: si WhatsApp rechaza el
    saludo, lo que el cliente escribe antes de que salga la respuesta llegó
    antes que ella (nota de continuación en el turno siguiente)."""
    from tests.test_sales_workflow_debounce import FIRST_CONTACT_GREETING, _final_resp

    wrote: list[str] = []

    async def send(handle, message, tracker):
        if message == FIRST_CONTACT_GREETING:
            if not wrote:  # el cliente escribe UNA vez, mientras sale el saludo
                wrote.append("y hacen envíos?")
                await handle.signal(HubaraSalesSessionWorkflowV2.send_message, args=[wrote[0], None, None])
            return []
        return _delivered(tracker, message)

    tracker = await _run_v2(
        tmp_path, send=send, llm_responses=[_final_resp(_REPLY), _final_resp("Sí, a todo el país 🤍")], prior_history=[]
    )

    assert [m for (_s, m) in tracker.send_whatsapp_calls][:1] == [_REPLY]
    assert "mientras preparabas tu respuesta anterior" in _second_turn_context(tracker)
