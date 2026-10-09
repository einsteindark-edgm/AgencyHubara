"""Lo que el texto promete, el turno lo hace (incidente del 2026-10-09).

El bot V2 cerró dos turnos con «Te paso el formulario para los datos de
envío» por `send_reply` y nunca llamó `request_shipping_details`. `send_reply`
graba en su resultado lo que el texto promete y el estado no cumple
(`promises`: kind, tools que lo cumplen, nota). Después del paso COMPLETO, si
ninguna tool del turno que salió (no rechazada) lo cumple, el texto se retiene
y hay UNA ronda más con la nota: el modelo llama la tool y reenvía su texto.
El orden del paso no importa (`[send_reply, formulario]` cumple). Si el modelo
insiste sin la tool, el texto sale (una sola ronda; la red de la activity
sigue detrás para el formulario y las tarifas). Sin `promises` grabadas el
turno es el de siempre. Aplica a los dos bots de ventas (V1 y V2 comparten
`send_reply` y `run_agent_turn`, detrás del parche); remarketing (sin
`send_reply`), ETA y las historias viejas no se enteran.
"""
from __future__ import annotations

import json
from typing import Any

from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from exoclaw_temporal.config import (
    BuildPromptInput,
    ExecuteToolInput,
    LLMChatInput,
    LLMConfig,
    LLMResponseData,
    RecordTurnInput,
    SessionInput,
    ToolCallData,
    WorkspaceConfig,
)
from src.platform.workflow_helpers import PendingMessage, run_agent_turn

QUEUE = "test-run-agent-turn-promised-actions"
PROMISE = "Perfecto, contra entrega. Te paso el formulario para los datos de envío 🤍"
NUDGE = "Le dijiste que le pasas el formulario de envío y no lo mandaste: llama request_shipping_details."


class _State:
    def __init__(self) -> None:
        self.replies: list[Any] = []
        self.calls: list[list[dict]] = []
        self.tools: list[str] = []
        self.form_rejected = False


STATE = _State()


@activity.defn(name="build_prompt")
async def _build_prompt(inp: BuildPromptInput) -> list[dict]:
    return [{"role": "system", "content": "s"}, {"role": "user", "content": inp.message}]


@activity.defn(name="llm_chat")
async def _llm_chat(inp: LLMChatInput) -> LLMResponseData:
    STATE.calls.append(list(inp.messages))
    n = len(STATE.calls)
    reply = STATE.replies[min(n - 1, len(STATE.replies) - 1)]
    if isinstance(reply, list):
        calls = [ToolCallData(id=f"c{n}_{i}", name=name, arguments=args) for i, (name, args) in enumerate(reply)]
        return LLMResponseData(content="", finish_reason="tool_calls", has_tool_calls=True, tool_calls=calls)
    return LLMResponseData(content=reply, finish_reason="stop", has_tool_calls=False, tool_calls=[])


@activity.defn(name="execute_tool")
async def _execute_tool(inp: ExecuteToolInput) -> str:
    """Como las tools reales: `send_reply` graba la promesa del formulario
    (sin mirar la cola, como sin vault); el formulario se encola o se niega."""
    STATE.tools.append(inp.name)
    if inp.name == "send_reply":
        text = inp.params.get("text", "")
        envelope: dict[str, Any] = {"reply": {"text": text}, "summary": "Mensaje listo para el cliente."}
        if "formulario" in text:
            envelope["promises"] = [{"kind": "formulario", "tools": ["request_shipping_details"], "nudge": NUDGE}]
        return json.dumps(envelope)
    if inp.name == "request_shipping_details":
        if STATE.form_rejected:
            return json.dumps({"queued": False, "error": "customer_deferred", "message": "No se mostró nada."})
        return json.dumps({"queued": True, "kind": "shipping_flow"})
    return json.dumps({"queued": True})


@activity.defn(name="record_turn")
async def _record_turn(inp: RecordTurnInput) -> None:
    return None


@workflow.defn(name="PromisedActionsProbeWorkflow")
class _PromisedActionsProbeWorkflow:
    @workflow.run
    async def run(self, max_iterations: int = 10) -> dict:
        session = SessionInput(
            session_id="wa_promises", channel="whatsapp", chat_id="wa_promises",
            llm=LLMConfig(model="fake", max_iterations=max_iterations),
            workspace=WorkspaceConfig(path="/tmp/ws"), tool_definitions_json="[]",
        )
        result = await run_agent_turn(
            session, PendingMessage(message="El contra entrega, y cuánto se demora en llegar?"), episode_id="ep_001",
        )
        return {
            "final": result.final_content,
            "guards": [s.get("name") for s in result.steps if s.get("kind") == "guard"],
        }


async def _turn(replies: list[Any], *, form_rejected: bool = False, max_iterations: int = 10) -> dict[str, Any]:
    STATE.__init__()
    STATE.replies = replies
    STATE.form_rejected = form_rejected
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=QUEUE, workflows=[_PromisedActionsProbeWorkflow],
            activities=[_build_prompt, _llm_chat, _execute_tool, _record_turn],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            return await env.client.execute_workflow(
                _PromisedActionsProbeWorkflow.run, max_iterations, id="promised-actions", task_queue=QUEUE
            )


def _system_notes() -> list[str]:
    return [m["content"] for call in STATE.calls for m in call if m.get("role") == "system" and m["content"] != "s"]


async def test_a_promise_without_its_tool_gets_one_more_round() -> None:
    out = await _turn([
        [("send_reply", {"text": PROMISE})],
        [("request_shipping_details", {"items": [{"handle": "calabaza", "quantity": 1}]}),
         ("send_reply", {"text": PROMISE})],
    ])

    assert out["final"] == PROMISE
    assert "promised_action_round" in out["guards"]
    assert STATE.tools == ["send_reply", "request_shipping_details", "send_reply"]
    assert any(NUDGE in note and "NO se envió" in note for note in _system_notes())


async def test_the_tool_in_the_same_step_keeps_the_promise_in_any_order() -> None:
    out = await _turn([[("send_reply", {"text": PROMISE}),
                        ("request_shipping_details", {"items": [{"handle": "calabaza", "quantity": 1}]})]])

    assert out["final"] == PROMISE
    assert "promised_action_round" not in out["guards"]
    assert len(STATE.calls) == 1


async def test_a_rejected_tool_does_not_keep_the_promise() -> None:
    """El formulario se negó en un paso anterior (el cliente aplazó): el texto
    que igual lo promete no sale; el modelo lee la nota y responde otra cosa."""
    out = await _turn(
        [[("request_shipping_details", {"items": []})],
         [("send_reply", {"text": PROMISE})],
         [("send_reply", {"text": "Claro, aquí te espero 🤍"})]],
        form_rejected=True,
    )

    assert out["guards"] == ["no_cut_tool_rejected", "promised_action_round"]
    assert out["final"] == "Claro, aquí te espero 🤍"


async def test_a_model_that_insists_sends_its_text_after_one_round() -> None:
    out = await _turn([[("send_reply", {"text": PROMISE})]])

    assert out["final"] == PROMISE
    assert out["guards"].count("promised_action_round") == 1
    assert len(STATE.calls) == 2


async def test_a_text_without_promises_is_the_turn_of_always() -> None:
    out = await _turn([[("send_reply", {"text": "En Bogotá llega en 1 a 2 días hábiles 🤍"})]])

    assert out["final"] == "En Bogotá llega en 1 a 2 días hábiles 🤍"
    assert out["guards"] == []
    assert len(STATE.calls) == 1


async def test_the_held_text_goes_out_when_the_round_only_sends_the_form() -> None:
    """Revisión del premortem (2026-10-09): si el modelo responde la ronda solo
    con el formulario (que corta el turno esperando al cliente), el texto
    retenido —la respuesta a lo que el cliente preguntó— no salía."""
    out = await _turn([
        [("send_reply", {"text": PROMISE})],
        [("request_shipping_details", {"items": [{"handle": "calabaza", "quantity": 1}]})],
    ])

    assert out["final"] == PROMISE
    assert "promised_action_round" in out["guards"]


async def test_no_round_on_the_last_iteration() -> None:
    """Sin iteraciones para la ronda, el texto sale (no el «se me cortó»)."""
    out = await _turn([[("send_reply", {"text": PROMISE})]], max_iterations=1)

    assert out["final"] == PROMISE
    assert "promised_action_round" not in out["guards"]


def test_the_memory_does_not_keep_the_promise_nudges() -> None:
    """Revisión del premortem (2026-10-09): el resultado grabado de `send_reply`
    llevaba «no lo mandaste: llama request_shipping_details»; en el turno
    siguiente el modelo lo releía y mandaba el formulario otra vez aunque la
    ronda o la red ya lo hubieran mandado. La nota vive solo en su turno."""
    from src.platform.workflow_helpers import _history_view

    recorded = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "send_reply", "arguments": json.dumps({"text": PROMISE})}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "name": "send_reply", "content": json.dumps({
            "reply": {"text": PROMISE}, "summary": "Mensaje listo.",
            "promises": [{"kind": "formulario", "tools": ["request_shipping_details"], "nudge": NUDGE}],
        })},
    ]

    view = _history_view(recorded, {"c1": PROMISE})

    stored = json.loads(view[1]["content"])
    assert "promises" not in stored and stored["reply"] == {"text": PROMISE}
