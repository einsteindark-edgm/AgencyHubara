"""Al agotar las rondas, el bot responde: nunca la disculpa fabricada
(laboratorio del PR #426, 2026-10-14).

«¿Tienen velas de cumpleaños con números para el pastel?» (algo que no
existe): el modelo llamó `search_products` 19 veces, agotó `max_iterations` y
el turno le mandó al cliente «¡Perdón! Justo se me cortó un segundito. ¿Me
repites lo que necesitabas?»: una falla técnica contada como si fuera de una
persona, y el cliente se quedaba sin respuesta (regla del operador: un error
interno nunca se le cuenta al cliente). Ahora, en un turno que el cliente
espera, hay UNA ronda de cierre con solo `send_reply` (sus controles siguen) y
una nota: «ya buscaste; respóndele con lo que encontraste o dile que no lo
tenemos». Si ni así responde, no sale nada (y queda la alerta). Los turnos
proactivos (sin disculpa) no cambian.
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

QUEUE = "test-run-agent-turn-closing-round"
FABRICATED = "se me cortó"
ANSWER = "No manejamos velas con números para pastel 🤍 ¿Te muestro las velas de deseos?"
TOOLS = json.dumps([
    {"type": "function", "function": {"name": "search_products", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "send_reply", "parameters": {"type": "object", "properties": {}}}},
])


class _State:
    def __init__(self) -> None:
        self.calls: list[LLMChatInput] = []
        self.answers_when_only_reply = True


STATE = _State()


@activity.defn(name="build_prompt")
async def _build_prompt(inp: BuildPromptInput) -> list[dict]:
    return [{"role": "system", "content": "s"}, {"role": "user", "content": inp.message}]


@activity.defn(name="llm_chat")
async def _llm_chat(inp: LLMChatInput) -> LLMResponseData:
    """Busca sin parar mientras tenga `search_products`; con solo `send_reply`, responde."""
    STATE.calls.append(inp)
    n = len(STATE.calls)
    names = {t["function"]["name"] for t in inp.tool_definitions()}
    if "search_products" in names:
        call = ToolCallData(id=f"c{n}", name="search_products", arguments={"q": f"velas números {n}"})
        return LLMResponseData(content="", finish_reason="tool_calls", has_tool_calls=True, tool_calls=[call])
    if STATE.answers_when_only_reply and names == {"send_reply"}:
        call = ToolCallData(id=f"c{n}", name="send_reply", arguments={"text": ANSWER})
        return LLMResponseData(content="", finish_reason="tool_calls", has_tool_calls=True, tool_calls=[call])
    return LLMResponseData(content="", finish_reason="stop", has_tool_calls=False, tool_calls=[])


@activity.defn(name="execute_tool")
async def _execute_tool(inp: ExecuteToolInput) -> str:
    if inp.name == "send_reply":
        return json.dumps({"reply": {"text": inp.params.get("text", "")}, "summary": "Mensaje listo."}, ensure_ascii=False)
    return json.dumps({"query": inp.params.get("q"), "count": 0, "results": []})


@activity.defn(name="record_turn")
async def _record_turn(inp: RecordTurnInput) -> None:
    return None


@workflow.defn(name="ClosingRoundProbeWorkflow")
class _ClosingRoundProbeWorkflow:
    @workflow.run
    async def run(self, fabricate: bool) -> dict:
        session = SessionInput(
            session_id="wa_closing", channel="whatsapp", chat_id="wa_closing",
            llm=LLMConfig(model="fake", max_iterations=3),
            workspace=WorkspaceConfig(path="/tmp/ws"), tool_definitions_json=TOOLS,
        )
        result = await run_agent_turn(
            session, PendingMessage(message="¿Tienen velas de cumpleaños con números para el pastel?"),
            episode_id="ep_001", fabricate_fallback_on_empty=fabricate,
        )
        return {
            "final": result.final_content,
            "guards": [s.get("name") for s in result.steps if s.get("kind") == "guard"],
        }


async def _turn(*, fabricate: bool = True, answers: bool = True) -> dict[str, Any]:
    STATE.__init__()
    STATE.answers_when_only_reply = answers
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=QUEUE, workflows=[_ClosingRoundProbeWorkflow],
            activities=[_build_prompt, _llm_chat, _execute_tool, _record_turn],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            return await env.client.execute_workflow(
                _ClosingRoundProbeWorkflow.run, fabricate, id="closing-round", task_queue=QUEUE
            )


async def test_out_of_rounds_the_customer_gets_an_answer_not_a_fabricated_apology() -> None:
    out = await _turn()

    assert out["final"] == ANSWER
    assert FABRICATED not in out["final"]
    last = STATE.calls[-1]
    assert {t["function"]["name"] for t in last.tool_definitions()} == {"send_reply"}
    assert any(m.get("role") == "system" and "no lo tenemos" in str(m.get("content")) for m in last.messages)
    assert "closing_round" in out["guards"]


async def test_if_it_still_does_not_answer_nothing_is_made_up() -> None:
    out = await _turn(answers=False)

    assert out["final"] == ""


async def test_a_proactive_turn_keeps_its_abstention() -> None:
    out = await _turn(fabricate=False)

    assert out["final"] == ""
    assert all({t["function"]["name"] for t in c.tool_definitions()} != {"send_reply"} for c in STATE.calls)
