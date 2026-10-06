"""La ráfaga espera la foto que el ingest está leyendo (texto ANTES de la foto).

2026-09-30 (laboratorio 4567, pedido del operador): el ingest ya retiene el
texto que llega DESPUÉS de una foto mientras la visión la lee. Al revés no:
«¿tienes esta?» llega primero, la ráfaga del workflow cierra tras 1,5 s de
silencio y la foto (que la visión tarda 2 a 8 s en leer) entra como otro
turno: el bot contesta sin la foto y después otra vez.

Ahora el ingest avisa al workflow cuando empieza a leer una foto
(`photo_reading(wamid)`) y cuando la foto ya entró (`done=True`):
* si la ráfaga iba a cerrar con una foto leyéndose, la espera (con tope);
* si la foto empieza a leerse mientras el modelo piensa y el turno todavía no
  le mostró nada al cliente, el turno vuelve a empezar con la foto (como un
  mensaje nuevo del cliente).
Una foto que nunca llega no retiene la ráfaga más que el tope.
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
    SALES_QUEUE,
    Tracker,
    _final_resp,
    _make_fake_activities,
)

V2_EXCLUDED: dict[str, str] = {}
_sales_workflow_version = sales_workflow_versions(__name__, V2_EXCLUDED)

TEXT = "¿tienes esta en azul?"
PHOTO = "[el cliente envió una foto: vela lila con pájaros; es el Velón Gorrión]"
WAMID = "wamid.PHOTO1"


def _user_messages(tracker: Tracker) -> list[str]:
    return [c.message for c in tracker.build_prompt_calls if "GHOST" not in c.message]


async def _start(env: WorkflowEnvironment, tracker: Tracker, tmp_path: Path, session: str, **fakes):
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    worker = Worker(
        env.client,
        task_queue=SALES_QUEUE,
        workflows=[HubaraSalesSessionWorkflow],
        activities=_make_fake_activities(tracker, workspace_path=str(workspace), **fakes),
    )
    return worker, workspace


@pytest.mark.asyncio
async def test_the_text_before_a_photo_waits_for_it_in_the_same_burst(tmp_path: Path) -> None:
    tracker = Tracker()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        worker, workspace = await _start(env, tracker, tmp_path, "wa_photo_after")
        async with worker:
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(session_id="wa_photo_after", runtime_workspace_path=str(workspace)),
                id="session-wa_photo_after",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(HubaraSalesSessionWorkflow.send_message, args=[TEXT, None, None])
            await handle.signal("photo_reading", args=[WAMID, False])
            # La visión tarda: pasan más de los 1,5 s de silencio de la ráfaga.
            await env.sleep(6)
            await handle.signal(HubaraSalesSessionWorkflow.send_message, args=[PHOTO, None, None])
            await handle.signal("photo_reading", args=[WAMID, True])
            await handle.result()

    messages = _user_messages(tracker)
    assert len(messages) == 1, messages
    assert TEXT in messages[0] and PHOTO in messages[0]


@pytest.mark.asyncio
async def test_a_photo_that_never_arrives_does_not_hold_the_burst(tmp_path: Path) -> None:
    tracker = Tracker()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        worker, workspace = await _start(env, tracker, tmp_path, "wa_photo_lost")
        async with worker:
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(session_id="wa_photo_lost", runtime_workspace_path=str(workspace)),
                id="session-wa_photo_lost",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(HubaraSalesSessionWorkflow.send_message, args=[TEXT, None, None])
            await handle.signal("photo_reading", args=[WAMID, False])
            await handle.result()

    assert [TEXT in m for m in _user_messages(tracker)] == [True]
    assert tracker.send_whatsapp_calls, "el texto quedó sin respuesta"


@pytest.mark.asyncio
async def test_a_photo_that_starts_while_the_model_thinks_restarts_the_turn_with_it(tmp_path: Path) -> None:
    """El cliente escribió, la ráfaga cerró y el modelo está pensando cuando
    llega la foto: el turno todavía no le mostró nada, así que vuelve a
    empezar y espera la foto (una sola respuesta que ve las dos cosas)."""
    tracker = Tracker()
    box: dict = {}

    async def _photo_starts() -> None:
        await box["handle"].signal("photo_reading", args=[WAMID, False])

    async with await WorkflowEnvironment.start_time_skipping() as env:
        worker, workspace = await _start(
            env,
            tracker,
            tmp_path,
            "wa_photo_mid",
            llm_responses=[_final_resp("Respuesta sin la foto"), _final_resp("Sí, el Velón Gorrión viene en azul")],
            llm_call_hooks={1: _photo_starts},
        )
        async with worker:
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(session_id="wa_photo_mid", runtime_workspace_path=str(workspace)),
                id="session-wa_photo_mid",
                task_queue=SALES_QUEUE,
            )
            box["handle"] = handle
            await handle.signal(HubaraSalesSessionWorkflow.send_message, args=[TEXT, None, None])
            await env.sleep(6)
            await handle.signal(HubaraSalesSessionWorkflow.send_message, args=[PHOTO, None, None])
            await handle.signal("photo_reading", args=[WAMID, True])
            await handle.result()

    texts = [m for (_s, m) in tracker.send_whatsapp_calls]
    assert all("Respuesta sin la foto" not in t for t in texts), texts
    assert any("viene en azul" in t for t in texts), texts
    recomposed = _user_messages(tracker)[-1]
    assert TEXT in recomposed and PHOTO in recomposed
    restarts = [s for t in tracker.turn_traces for s in t.get("steps") or [] if s.get("kind") == "restart"]
    assert [s.get("photo") for s in restarts] == [True]
