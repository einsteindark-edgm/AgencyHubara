"""Traza v2 del bot actual (plan del laboratorio, PR 2): los pasos del turno en orden.

El modal del hilo (plan §11) dibuja cada turno como un diagrama de secuencia:
Cliente → Workflow → LLM → Workflow → Tools → … → Cliente. Para eso la traza
tiene que decir QUÉ pasó y EN QUÉ ORDEN: cada `llm_chat` con su ronda y qué
pasó con su texto, cada `execute_tool` con su resultado, cada corte del turno,
las guardas en el orden en que actuaron (v1 las guardaba ordenadas
alfabéticamente: el orden se perdía), los reinicios por corrientazo y las
burbujas que salieron con su wamid.

Todo se arma en memoria del workflow: no agrega commands a la history, así que
no necesita `workflow.patched` (L-22). Las histories viejas devuelven `None`
del envío y un int del flush; el workflow acepta las dos formas.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from exoclaw_temporal.config import LLMResponseData, ToolCallData
from src.plugins.chats.agent.sales.contracts import SalesSessionInput
from src.plugins.chats.agent.sales.workflows.sales_session import HubaraSalesSessionWorkflow
from tests.test_sales_workflow_debounce import (
    SALES_QUEUE,
    Tracker,
    _final_resp,
    _make_fake_activities,
)

_PRIOR = [
    {"role": "user", "content": "Hola"},
    {"role": "assistant", "content": "¡Buenas! Bienvenido a *Hubara*..."},
]


async def _run(
    tracker: Tracker,
    tmp_path: Path,
    *,
    messages: list[str],
    session: str = "wa_steps",
    llm_call_hooks_factory=None,
    **fakes,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    handle_box: dict = {}
    hooks = llm_call_hooks_factory(handle_box) if llm_call_hooks_factory else None
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=_make_fake_activities(
                tracker,
                workspace_path=str(workspace),
                prior_history=_PRIOR,
                llm_call_hooks=hooks,
                **fakes,
            ),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(session_id=session, runtime_workspace_path=str(workspace)),
                id=f"session-{session}",
                task_queue=SALES_QUEUE,
            )
            handle_box["handle"] = handle
            for m in messages:
                await handle.signal(HubaraSalesSessionWorkflow.send_message, args=[m, None, None])
            await handle.result()


def _customer_trace(tracker: Tracker) -> dict:
    return next(t for t in tracker.turn_traces if t["trigger"] == "customer")


def _search_then_answer() -> list[LLMResponseData]:
    return [
        LLMResponseData(
            content="Voy a buscar qué tenemos de café",
            finish_reason="tool_calls",
            has_tool_calls=True,
            tool_calls=[ToolCallData(id="call_1", name="search_products", arguments={"q": "café"})],
            usage={"prompt_tokens": 1000, "completion_tokens": 20},
        ),
        _final_resp("Tenemos la vela de café en frasco ámbar"),
    ]


@pytest.mark.asyncio
async def test_steps_follow_the_turn_llm_tool_llm_and_the_bubble_that_went_out(tmp_path: Path) -> None:
    tracker = Tracker()

    await _run(
        tracker,
        tmp_path,
        messages=["¿tienen de café?"],
        llm_responses=_search_then_answer(),
        tool_results={"search_products": json.dumps({"query": "café", "count": 3, "results": []})},
    )

    trace = _customer_trace(tracker)
    steps = trace["steps"]
    assert [s["kind"] for s in steps] == ["llm", "tool", "llm", "outbound"]
    first, tool, second, outbound = steps
    assert (first["round"], first["finish"], first["tool_calls"]) == (1, "tool_calls", ["search_products"])
    assert (first["tokens_in"], first["tokens_out"]) == (1000, 20)
    assert first["text_fate"] == "discarded_default_deny"
    assert first["text"] == "Voy a buscar qué tenemos de café"
    assert (tool["name"], tool["call_id"], tool["ok"]) == ("search_products", "call_1", True)
    assert "count:3" in tool["notes"]
    assert (second["round"], second["text_fate"]) == (2, "final")
    assert outbound["bubbles"] == [
        {"kind": "text", "text": "Tenemos la vela de café en frasco ámbar", "wamid": "wamid.out1", "delivered": True}
    ]
    assert [s["i"] for s in steps] == [1, 2, 3, 4]
    assert all(isinstance(s["at_ms"], int) and s["at_ms"] >= 0 for s in steps)
    assert [s["at_ms"] for s in steps] == sorted(s["at_ms"] for s in steps)
    assert trace["turn_key"].startswith("run:") and trace["turn_key"].endswith("/t:1")
    assert (trace["source"], trace["mode"]) == ("prod", "off")


@pytest.mark.asyncio
async def test_guards_keep_the_order_in_which_they_acted(tmp_path: Path) -> None:
    """La guarda de variantes actúa ANTES que la de texto administrativo. En v1
    el campo `guards` las ordena alfabéticamente y el orden se pierde; `steps`
    lo conserva, con el texto antes y después de cada una."""
    tracker = Tracker()
    # Acuse administrativo (run b06636a6): ninguna guarda lo puede rescatar.
    leaky = "Etiqueta registrada."

    await _run(
        tracker,
        tmp_path,
        messages=["Opciones"],
        llm_responses=[_final_resp(leaky)],
        variant_guard_result=True,
    )

    trace = _customer_trace(tracker)
    guards = [s for s in trace["steps"] if s["kind"] == "guard"]
    names = [g["name"] for g in guards]
    assert names.index("variant_enumeration_guard") < names.index("admin_text_guard")
    variant = guards[names.index("variant_enumeration_guard")]
    assert variant["before"] == leaky and variant["after"] == ""
    # El campo v1 sigue igual: ordenado y sin repetidos.
    assert trace["guards"] == sorted(set(trace["guards"]))
    assert "variant_enumeration_guard" in trace["guards"]


@pytest.mark.asyncio
async def test_a_restarted_turn_keeps_the_aborted_attempt_and_the_restart(tmp_path: Path) -> None:
    """Corrientazo (run eda8d460): el cliente escribe mientras el LLM piensa y
    el turno se reinicia. La traza muestra el intento abortado, el corte en el
    Checkpoint A, el reinicio y el intento que sí respondió."""
    tracker = Tracker()

    def hooks(box: dict) -> dict:
        async def _signal_mid_llm() -> None:
            await box["handle"].signal(
                HubaraSalesSessionWorkflow.send_message, args=["y el envío a Bogotá", None, None]
            )

        return {1: _signal_mid_llm}

    await _run(
        tracker,
        tmp_path,
        messages=["¿me mandas el catálogo?"],
        llm_responses=[_final_resp("solo el primer mensaje"), _final_resp("catálogo y envío")],
        llm_call_hooks_factory=hooks,
    )

    trace = _customer_trace(tracker)
    kinds = [s["kind"] for s in trace["steps"]]
    assert kinds == ["llm", "cut", "restart", "llm", "outbound"]
    cut, restart = trace["steps"][1], trace["steps"][2]
    assert cut["reason"] == "checkpoint_a"
    assert (restart["reason"], restart["attempt"], restart["drained"]) == ("checkpoint_a", 1, 1)


@pytest.mark.asyncio
async def test_turn_that_waits_for_the_customer_records_the_cut_and_the_flush(tmp_path: Path) -> None:
    """L-11: el selector de variantes deja la conversación esperando al cliente
    y el turno se corta ahí. El flush entrega el componente con su wamid."""
    tracker = Tracker()

    await _run(
        tracker,
        tmp_path,
        messages=["quiero la Cubo Love"],
        llm_responses=[
            LLMResponseData(
                content="",
                finish_reason="tool_calls",
                has_tool_calls=True,
                tool_calls=[ToolCallData(id="call_9", name="present_variant_picker", arguments={"slot": "aroma"})],
            )
        ],
        tool_results={"present_variant_picker": json.dumps({"queued": True})},
        flush_results=[{"kind": "variant_picker", "wamid": "wamid.pick", "ok": True}],
    )

    trace = _customer_trace(tracker)
    kinds = [s["kind"] for s in trace["steps"]]
    assert kinds == ["llm", "tool", "cut", "outbound"]
    assert trace["steps"][2]["reason"] == "awaits_customer"
    assert trace["steps"][2]["tools"] == ["present_variant_picker"]
    assert trace["steps"][3]["bubbles"] == [{"kind": "variant_picker", "wamid": "wamid.pick", "delivered": True}]


@pytest.mark.asyncio
async def test_old_activity_shapes_still_produce_the_trace(tmp_path: Path) -> None:
    """Replay de histories anteriores: el envío devolvía `None` y el flush un
    int. La burbuja se registra igual, sin wamid y sin confirmar entrega."""
    tracker = Tracker()

    await _run(
        tracker,
        tmp_path,
        messages=["hola"],
        llm_responses=[_final_resp("¡Hola! ¿En qué te ayudo?")],
        send_returns_none=True,
    )

    outbound = [s for s in _customer_trace(tracker)["steps"] if s["kind"] == "outbound"]
    assert outbound[0]["bubbles"] == [{"kind": "text", "text": "¡Hola! ¿En qué te ayudo?", "delivered": None}]


def test_activity_return_types_still_decode_the_results_old_histories_recorded() -> None:
    """Temporal decodifica el resultado GRABADO con el tipo de retorno de la
    activity. Las histories anteriores a la traza v2 grabaron `None` (envío) y
    un int (flush): si alguien estrecha la anotación a `list[...]`, esas
    histories dejan de re-jugarse y los workflows vivos quedan colgados
    reintentando la tarea (se comprobó re-jugando 38 historias reales de
    producción con la anotación estrecha: fallan en `_convert_payloads`)."""
    from temporalio import activity
    from temporalio.converter import value_to_type

    from src.platform.whatsapp.activities import send_whatsapp_message_activity
    from src.plugins.chats.agent.sales.activities.flush_ui_intents import flush_pending_ui_intents_activity

    send_type = activity._Definition.must_from_callable(send_whatsapp_message_activity).ret_type
    flush_type = activity._Definition.must_from_callable(flush_pending_ui_intents_activity).ret_type

    assert value_to_type(send_type, None) is None
    assert value_to_type(send_type, [{"wamid": "w", "text": "t"}]) == [{"wamid": "w", "text": "t"}]
    assert value_to_type(flush_type, 2) == 2
    assert value_to_type(flush_type, [{"kind": "k", "wamid": None, "ok": False}]) == [
        {"kind": "k", "wamid": None, "ok": False}
    ]
