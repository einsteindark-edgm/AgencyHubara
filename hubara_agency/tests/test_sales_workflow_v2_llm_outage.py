"""El turno de ventas V2 sobrevive a una caída total del LLM (2026-10-15).

DeepSeek se colgó y el respaldo no alcanzó a entrar: `llm_chat` agotó sus
reintentos, el `ActivityError` tumbó el workflow entero y los mensajes del
cliente quedaron sin respuesta (el equipo los levantó a mano). Temporal es el
respaldo: el turno espera con un timer durable y vuelve a llamar al modelo
mientras la ventana de 24 h lo permita y el chat siga siendo del bot.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from exoclaw_temporal.config import LLMChatInput, LLMResponseData
from src.plugins.chats.agent.sales.contracts import SalesSessionInput
from src.plugins.chats.agent.sales.decisions.contracts import EgressInput, EgressOutput
from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2
from tests.test_sales_workflow_debounce import SALES_QUEUE, Tracker, _make_fake_activities
from tests.test_sales_workflow_v2 import _RETURNING, _customer_trace, _run, _scripted_egress, _sent

_ANSWER = "¡Hola! Sí, tenemos velas de Halloween."


def _llm_down_for(
    calls: int,
    *,
    answer: str = _ANSWER,
    seen: list[datetime] | None = None,
    inputs: list[list[dict]] | None = None,
    on_first_failure=None,
):
    """`llm_chat` que falla del todo (DeepSeek y el respaldo sin responder) en
    sus primeras `calls` llamadas y después contesta `answer`. `seen`: la hora
    (del servidor de Temporal) de cada llamada; `inputs`: lo que recibió el
    modelo en cada una; `on_first_failure`: corre durante la primera falla
    (el cliente escribe mientras el modelo está caído)."""
    state = {"n": 0}

    @activity.defn(name="llm_chat")
    async def llm_chat(input: LLMChatInput) -> LLMResponseData:
        state["n"] += 1
        if seen is not None:
            seen.append(activity.info().scheduled_time)
        if inputs is not None:
            inputs.append(list(input.messages))
        if state["n"] == 1 and on_first_failure is not None:
            await on_first_failure()
        if state["n"] <= calls:
            raise ApplicationError("DeepSeek y gemini-backup no responden", non_retryable=True)
        return LLMResponseData(content=answer, finish_reason="stop", has_tool_calls=False, tool_calls=[])

    return llm_chat


def _reply_window(*, route: str = "ventas", closes_in: timedelta | None = timedelta(hours=20)):
    """`read_reply_window` falso: el chat es de `route` y la ventana cierra
    `closes_in` después de la primera lectura (None = no se sabe). Devuelve el
    JSON de `ReplyWindow` como dict, igual que el falso del laboratorio: el
    workflow lo recibe convertido al contrato."""
    box: dict[str, datetime | None] = {}

    @activity.defn(name="read_reply_window")
    async def read_reply_window(session_id: str) -> dict:
        if "closes_at" not in box:
            box["closes_at"] = activity.info().scheduled_time + closes_in if closes_in is not None else None
        closes_at = box["closes_at"]
        return {
            "route": route,
            "window_expires_at_ms": int(closes_at.timestamp() * 1000) if closes_at is not None else None,
        }

    return read_reply_window


async def _run_outage(tmp_path: Path, *, down_for: int = 0, llm=None, window=None, **kwargs) -> Tracker:
    return await _run(
        HubaraSalesSessionWorkflowV2,
        tmp_path,
        responses=[],
        replace=[llm or _llm_down_for(down_for)],
        extra=[window or _reply_window()],
        **kwargs,
    )


def _outage_steps(trace: dict) -> list[dict]:
    return [s for s in trace["steps"] if s["kind"] == "llm_outage"]


async def test_a_total_llm_outage_does_not_lose_the_customer_turn(tmp_path: Path) -> None:
    tracker = await _run_outage(tmp_path, down_for=2)

    assert _sent(tracker) == [_ANSWER]
    assert [s["retry"] for s in _outage_steps(_customer_trace(tracker))] == [True, True]


async def test_the_turn_waits_minutes_between_rounds_and_the_wait_grows(tmp_path: Path) -> None:
    seen: list[datetime] = []

    tracker = await _run_outage(tmp_path, llm=_llm_down_for(4, seen=seen))

    gaps = [round((after - before).total_seconds() / 60) for before, after in zip(seen, seen[1:])]
    assert gaps[:4] == [1, 2, 4, 8]
    assert _sent(tracker) == [_ANSWER]


async def test_the_turn_gives_up_without_sending_when_the_window_is_about_to_close(tmp_path: Path) -> None:
    tracker = await _run_outage(tmp_path, down_for=10, window=_reply_window(closes_in=timedelta(minutes=3)))

    assert _sent(tracker) == []
    trace = _customer_trace(tracker)
    assert trace["suppressed_reason"] == "llm_unavailable"
    assert [s["retry"] for s in _outage_steps(trace)] == [False]
    assert _gave_up_reason(trace) == "window_closing"


async def test_a_turn_that_gave_up_keeps_what_the_customer_wrote_for_the_next_turn(tmp_path: Path) -> None:
    tracker = await _run_outage(tmp_path, down_for=10, window=_reply_window(closes_in=timedelta(minutes=3)))

    customer_turn = tracker.record_turn_new_messages[0]
    assert {"role": "user", "content": "Hola, busco una vela"} in customer_turn
    assert not any(m["role"] == "assistant" for m in customer_turn)


async def test_once_a_person_took_the_chat_the_turn_sends_nothing_not_even_the_greeting(tmp_path: Path) -> None:
    """El operador entró a levantar el chat durante la caída: el bot no le habla
    encima. Un turno rendido no le pregunta nada al egreso, que en un primer
    contacto pediría el saludo."""
    asked: list[EgressInput] = []
    greeting_egress = _scripted_egress(EgressOutput(text="", greeting_needed=True), asked)

    tracker = await _run(
        HubaraSalesSessionWorkflowV2,
        tmp_path,
        responses=[],
        replace=[_llm_down_for(10), greeting_egress],
        extra=[_reply_window(route="humano")],
        prior_history=None,
    )

    assert _sent(tracker) == [] and asked == []
    assert _gave_up_reason(_customer_trace(tracker)) == "human_took_over"


async def test_a_turn_that_gave_up_neither_flushes_components_nor_asks_for_a_complement(tmp_path: Path) -> None:
    """Con las capas del motor encendidas, la verificación ③ de una respuesta
    vacía pediría un complemento: otro turno que, si el modelo ya volvió, le
    hablaría a un cliente que ahora atiende una persona."""
    from tests.test_sales_perception_layers import Classifier

    classifier = Classifier(decision="complement", missing=["envio"])

    tracker = await _run(
        HubaraSalesSessionWorkflowV2,
        tmp_path,
        responses=[],
        replace=[_llm_down_for(10)],
        extra=[_reply_window(route="humano"), *classifier.activities()],
        meta={"perception_mode": "on", "perception_profile": "jev-v1"},
    )

    assert classifier.perceived and classifier.verified == []
    assert tracker.flush_calls == 0
    assert "complement" not in [t["trigger"] for t in tracker.turn_traces]


async def test_a_remarketing_claim_left_by_the_same_outage_does_not_stop_the_wait(tmp_path: Path) -> None:
    """El ciclo de reactivación reclama el chat (`remarketing`) antes de su
    turno; con el LLM caído ese turno muere y la ruta queda pegada. El cliente
    sigue esperando su respuesta: el turno de ventas no se rinde por eso."""
    tracker = await _run_outage(tmp_path, down_for=2, window=_reply_window(route="remarketing"))

    assert _sent(tracker) == [_ANSWER]


async def test_what_the_customer_writes_during_the_outage_goes_in_the_same_reply(tmp_path: Path) -> None:
    box: dict = {}
    inputs: list[list[dict]] = []

    async def customer_writes_again() -> None:
        await box["handle"].signal(HubaraSalesSessionWorkflowV2.send_message, args=["¿y tienen portavelas?", None, None])

    tracker = await _run_outage(
        tmp_path, llm=_llm_down_for(1, inputs=inputs, on_first_failure=customer_writes_again), box=box
    )

    assert _sent(tracker) == [_ANSWER]
    answered = json.dumps(inputs[2], ensure_ascii=False)
    assert "Hola, busco una vela" in answered and "¿y tienen portavelas?" in answered


async def test_a_session_that_waited_out_an_outage_replays_its_own_history(tmp_path: Path) -> None:
    """R-DET: las esperas, las lecturas de la ventana y la vuelta del modelo
    re-juegan sin divergir."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    acts = [
        a
        for a in _make_fake_activities(tracker, workspace_path=str(workspace), prior_history=_RETURNING)
        if a.__temporal_activity_definition.name != "llm_chat"
    ]
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflowV2],
            activities=[*acts, _llm_down_for(2), _reply_window()],
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflowV2.run,
                SalesSessionInput(session_id="wa_v2", runtime_workspace_path=str(workspace)),
                id="session-wa_v2-outage-replay",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(HubaraSalesSessionWorkflowV2.send_message, args=["Hola, busco una vela", None, None])
            await handle.result()
            history = await handle.fetch_history()

    assert _sent(tracker) == [_ANSWER]
    await Replayer(workflows=[HubaraSalesSessionWorkflowV2]).replay_workflow(history)


def _gave_up_reason(trace: dict) -> str | None:
    return next((s.get("reason") for s in trace["steps"] if s.get("name") == "llm_unavailable"), None)
