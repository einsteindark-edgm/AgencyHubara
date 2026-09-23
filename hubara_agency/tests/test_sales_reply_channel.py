"""Canal único al cliente en el turno de ventas (run 28a8e407, fase 2).

El cliente lee solo lo que el modelo pasa en `send_reply(text)` (o en los
params de texto de otras tools). Contrato del turno:

  * el `reply.text` que devolvió la tool es el mensaje y el turno termina ahí;
  * el texto libre del modelo (su narración) nunca sale ni queda en el
    historial del LLM — verse narrando 20 veces por conversación era el
    few-shot que lo hacía narrar también en la respuesta;
  * si la tool rechazó el texto (nota interna), el modelo lo reescribe en
    el mismo turno.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from exoclaw_temporal.config import ExecuteToolInput, LLMResponseData, ToolCallData
from src.plugins.chats.agent.sales.contracts import SalesSessionInput
from src.plugins.chats.agent.sales.workflows.sales_session import HubaraSalesSessionWorkflow
from tests.test_sales_workflow_debounce import (
    SALES_QUEUE,
    Tracker,
    _final_resp,
    _make_fake_activities,
)

_NARRATION = "El cliente eligió Cubo Love. Le pregunto el aroma."


def _calls(*calls: tuple[str, dict], narration: str = "") -> LLMResponseData:
    return LLMResponseData(
        content=narration,
        finish_reason="tool_calls",
        has_tool_calls=True,
        tool_calls=[
            ToolCallData(id=f"c{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(calls, 1)
        ],
    )


def _reply(text: str) -> str:
    return json.dumps({"reply": {"text": text}, "summary": "Tu turno termina aquí."})


_REJECTED = json.dumps(
    {"sent": False, "error": "internal_text", "message": "Reescríbelo y vuelve a llamar send_reply."}
)


#: Cliente que ya conversó: el turno no es primer contacto (sin burbuja de
#: bienvenida de por medio).
_RETURNING = [
    {"role": "user", "content": "Hola"},
    {"role": "assistant", "content": "¡Buenas! Bienvenido a *Hubara*..."},
]


async def _run_turn(
    tracker: Tracker,
    tmp_path: Path,
    responses: list[LLMResponseData],
    results: dict[str, list[str]],
    *,
    prior_history: list[dict] | None = _RETURNING,
) -> None:
    """Un turno del cliente; `results[tool]` = resultados de esa tool en orden."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    queues = {name: list(values) for name, values in results.items()}

    @activity.defn(name="execute_tool")
    async def sequenced_execute_tool(input: ExecuteToolInput) -> str:
        tracker.execute_tool_calls.append(input.name)
        queue = queues.get(input.name) or []
        return queue.pop(0) if queue else "ok"

    activities = [
        a
        for a in _make_fake_activities(
            tracker,
            workspace_path=str(workspace),
            llm_responses=responses,
            prior_history=prior_history,
        )
        if getattr(a, "__temporal_activity_definition").name != "execute_tool"
    ]
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=[*activities, sequenced_execute_tool],
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(session_id="wa_reply", runtime_workspace_path=str(workspace)),
                id="session-wa_reply",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message, args=["Cubo love", None, None]
            )
            await handle.result()


def _sent(tracker: Tracker) -> list[str]:
    return [m for (_s, m) in tracker.send_whatsapp_calls]


def _customer_turn_llm_messages(tracker: Tracker) -> int:
    """Mensajes del LLM en el turno del cliente (el primero grabado; después
    corre el cierre por inactividad de la sesión de prueba)."""
    turn = tracker.record_turn_new_messages[0]
    return sum(1 for m in turn if m.get("role") == "assistant")


@pytest.mark.asyncio
async def test_the_reply_is_the_message_and_ends_the_turn(tmp_path: Path) -> None:
    tracker = Tracker()
    await _run_turn(
        tracker,
        tmp_path,
        [_calls(("send_reply", {"text": "¿Qué aroma te gustaría? 🤍"}), narration=_NARRATION)],
        {"send_reply": [_reply("¿Qué aroma te gustaría? 🤍")]},
    )

    assert _sent(tracker) == ["¿Qué aroma te gustaría? 🤍"]
    assert _customer_turn_llm_messages(tracker) == 1, (
        "tras send_reply no se le pide otro mensaje al LLM"
    )
    assert not any("El cliente eligió" in m for m in _sent(tracker))


@pytest.mark.asyncio
async def test_a_rejected_reply_is_rewritten_in_the_same_turn(tmp_path: Path) -> None:
    tracker = Tracker()
    await _run_turn(
        tracker,
        tmp_path,
        [
            _calls(("send_reply", {"text": "El cliente pregunta el aroma. Le respondo."})),
            _calls(("send_reply", {"text": "¿Qué aroma te gustaría? 🤍"})),
        ],
        {"send_reply": [_REJECTED, _reply("¿Qué aroma te gustaría? 🤍")]},
    )

    assert _sent(tracker) == ["¿Qué aroma te gustaría? 🤍"]
    assert _customer_turn_llm_messages(tracker) == 2


@pytest.mark.asyncio
async def test_history_keeps_the_reply_but_not_the_narration(tmp_path: Path) -> None:
    tracker = Tracker()
    await _run_turn(
        tracker,
        tmp_path,
        [
            _calls(("set_order_slot", {"producto": "Cubo Love"}), narration="Registro el producto."),
            _calls(
                ("send_reply", {"text": "El cliente eligió.\n\n¿Qué aroma te gustaría? 🤍"}),
                narration=_NARRATION,
            ),
        ],
        {"send_reply": [_reply("¿Qué aroma te gustaría? 🤍")]},
    )

    turn = tracker.record_turn_new_messages[0]
    assistant_calls = [m for m in turn if m.get("role") == "assistant"]
    assert [m.get("content") for m in assistant_calls] == ["", ""]
    reply_call = assistant_calls[-1]["tool_calls"][0]
    assert reply_call["function"]["name"] == "send_reply"
    assert json.loads(reply_call["function"]["arguments"]) == {
        "text": "¿Qué aroma te gustaría? 🤍"
    }, "el historial guarda lo que el cliente recibió"
    dump = json.dumps(turn, ensure_ascii=False)
    assert "Registro el producto" not in dump
    assert "El cliente eligió" not in dump


@pytest.mark.asyncio
async def test_reply_next_to_the_catalog_goes_before_the_menu(tmp_path: Path) -> None:
    tracker = Tracker()
    await _run_turn(
        tracker,
        tmp_path,
        [
            _calls(
                ("send_reply", {"text": "Estas son las de Amor y Amistad 🤍"}),
                ("present_products", {"handles": ["cubo-love"], "intro_text": "Mira:"}),
            )
        ],
        {
            "send_reply": [_reply("Estas son las de Amor y Amistad 🤍")],
            "present_products": [json.dumps({"queued": True, "kind": "products_list"})],
        },
    )

    assert _sent(tracker) == ["Estas son las de Amor y Amistad 🤍"]
    send_at = tracker.timeline.index("send:Estas son las de Amor y Amistad 🤍")
    assert send_at < tracker.timeline.index("flush")


@pytest.mark.asyncio
async def test_plain_final_text_still_works_while_the_model_learns_the_tool(
    tmp_path: Path,
) -> None:
    """Transición: historiales viejos enseñan a responder en texto libre. Ese
    camino sigue (con el rescate y el filtro de siempre) mientras tanto."""
    tracker = Tracker()
    await _run_turn(tracker, tmp_path, [_final_resp("¿Qué aroma te gustaría?")], {})

    assert _sent(tracker) == ["¿Qué aroma te gustaría?"]


@pytest.mark.asyncio
async def test_first_contact_reply_that_greets_gets_no_extra_welcome(tmp_path: Path) -> None:
    """`send_reply` cuenta como texto que llega al cliente: si ya saluda, el
    workflow no inyecta la burbuja de bienvenida encima."""
    tracker = Tracker()
    greeting = "¡Buenas tardes! Bienvenido a *Hubara* 🤍 ¿Qué estás buscando?"
    await _run_turn(
        tracker,
        tmp_path,
        [_calls(("send_reply", {"text": greeting}))],
        {"send_reply": [_reply(greeting)]},
        prior_history=None,
    )

    assert _sent(tracker) == [greeting]


@pytest.mark.asyncio
async def test_reply_written_next_to_a_failed_tool_is_not_sent(tmp_path: Path) -> None:
    """El modelo escribió "¡Listo, quedó registrado!" en el MISMO paso en que
    `register_order` rebotó: esa respuesta no sale; lee el error y responde
    de nuevo."""
    tracker = Tracker()
    await _run_turn(
        tracker,
        tmp_path,
        [
            _calls(
                ("register_order", {"items": []}),
                ("send_reply", {"text": "¡Listo, tu pedido quedó registrado! 🤍"}),
            ),
            _calls(("send_reply", {"text": "El precio del set cambió a $21.000. ¿Te lo confirmo así?"})),
        ],
        {
            "register_order": [json.dumps({"registered": False, "error": "price_mismatch"})],
            "send_reply": [
                _reply("¡Listo, tu pedido quedó registrado! 🤍"),
                _reply("El precio del set cambió a $21.000. ¿Te lo confirmo así?"),
            ],
        },
    )

    assert _sent(tracker) == ["El precio del set cambió a $21.000. ¿Te lo confirmo así?"]
    turn = json.dumps(tracker.record_turn_new_messages[0], ensure_ascii=False)
    assert "NO se envió" in turn, "el modelo tiene que saber que ese texto no salió"
