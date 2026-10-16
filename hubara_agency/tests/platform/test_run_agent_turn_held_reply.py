"""Una respuesta que volvió al modelo no se pierde porque una tarjeta cortó el
turno (premortem del PR #426, 2026-10-13).

`send_reply` devuelve el texto al modelo UNA vez por mensaje del cliente
(«No se envió: …», `sent: false`) cuando no coincide con el catálogo o el
pedido, cuando era nota interna, etc. Si en el MISMO paso el modelo llamó una
tarjeta que deja al cliente esperando (selector, botones, formulario), la
tarjeta salía y el corte L-11 terminaba el turno: el cliente recibía solo la
tarjeta y el modelo nunca leía por qué su texto no salió. Ahora hay una ronda
más (la tarjeta ya salió): el modelo corrige su texto y ese sale.
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

QUEUE = "test-run-agent-turn-held-reply"
WRONG = "Las de deseos cuadradas vienen en Chanel y Drakar. ¿Cuál prefieres?"
RIGHT = "Las de deseos cuadradas vienen en Noche de Navidad 🎄"
HELD = "No se envió: «Chanel» y «Drakar» no son aromas de Velas de deseos cuadradas."


class _State:
    def __init__(self) -> None:
        self.replies: list[Any] = []
        self.calls: list[list[dict]] = []
        self.tools: list[str] = []
        self.held_once = False


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
    """Como las tools reales: la primera vez `send_reply` retiene el texto
    equivocado (una retención por mensaje); la tarjeta se encola."""
    STATE.tools.append(inp.name)
    if inp.name == "send_reply":
        text = inp.params.get("text", "")
        if text == WRONG and not STATE.held_once:
            STATE.held_once = True
            return json.dumps({"sent": False, "error": "fact_check", "message": HELD}, ensure_ascii=False)
        return json.dumps({"reply": {"text": text}, "summary": "Mensaje listo para el cliente."})
    return json.dumps({"queued": True})


@activity.defn(name="record_turn")
async def _record_turn(inp: RecordTurnInput) -> None:
    return None


@workflow.defn(name="HeldReplyProbeWorkflow")
class _HeldReplyProbeWorkflow:
    @workflow.run
    async def run(self) -> dict:
        session = SessionInput(
            session_id="wa_held", channel="whatsapp", chat_id="wa_held",
            llm=LLMConfig(model="fake", max_iterations=10),
            workspace=WorkspaceConfig(path="/tmp/ws"), tool_definitions_json="[]",
        )
        result = await run_agent_turn(session, PendingMessage(message="¿En qué aromas vienen?"), episode_id="ep_001")
        return {
            "final": result.final_content,
            "guards": [s.get("name") for s in result.steps if s.get("kind") == "guard"],
        }


async def _turn(replies: list[Any]) -> dict[str, Any]:
    STATE.__init__()
    STATE.replies = replies
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=QUEUE, workflows=[_HeldReplyProbeWorkflow],
            activities=[_build_prompt, _llm_chat, _execute_tool, _record_turn],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            return await env.client.execute_workflow(_HeldReplyProbeWorkflow.run, id="held-reply", task_queue=QUEUE)


PICKER = ("present_variant_picker", {"handle": "velas-de-deseos-cuadradas", "variant_type": "aroma"})


async def test_a_held_reply_next_to_a_card_that_ends_the_turn_gets_one_more_round() -> None:
    out = await _turn([[("send_reply", {"text": WRONG}), PICKER], [("send_reply", {"text": RIGHT})]])

    assert out["final"] == RIGHT
    assert STATE.tools == ["send_reply", "present_variant_picker", "send_reply"]
    assert any(HELD in str(m.get("content")) for m in STATE.calls[1])  # el modelo leyó por qué
    assert "held_reply_round" in out["guards"]


async def test_the_card_alone_still_ends_the_turn() -> None:
    out = await _turn([[PICKER], [("send_reply", {"text": RIGHT})]])

    assert out["final"] == ""
    assert STATE.tools == ["present_variant_picker"]


async def test_a_reply_that_went_out_next_to_the_card_ends_the_turn() -> None:
    out = await _turn([[("send_reply", {"text": RIGHT}), PICKER], [("send_reply", {"text": "otra cosa"})]])

    assert out["final"] == RIGHT
    assert STATE.tools == ["send_reply", "present_variant_picker"]
