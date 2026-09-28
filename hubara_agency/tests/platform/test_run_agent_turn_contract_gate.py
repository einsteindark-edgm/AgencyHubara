"""La segunda puerta del turno (motor de decisiones, F6): si el LLM va a
cerrar el turno con un texto SIN usar una tool que el contrato pedía y el
turno todavía no le mostró nada al cliente, hay UNA ronda más con una nota
que nombra la tool que falta. El borrador descartado no se graba en el
historial (el LLM no recuerda lo que nunca salió).

La nota la arma el caller desde el resultado GRABADO del motor
(`TurnPolicy.final_round_note`); sin ella (V1, remarketing, ETA y las
historias viejas) el turno es el de siempre.
"""
from __future__ import annotations

from typing import Any

from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from exoclaw_temporal.config import (
    BuildPromptInput,
    LLMChatInput,
    LLMConfig,
    LLMResponseData,
    RecordTurnInput,
    SessionInput,
    WorkspaceConfig,
)
from src.platform.workflow_helpers import PendingMessage, TurnPolicy, run_agent_turn

QUEUE = "test-run-agent-turn-contract-gate"
NOTE = "[CONTRATO DEL TURNO] Antes de responder: el precio sale del catálogo, usa search_products."


class _State:
    def __init__(self) -> None:
        self.replies: list[str] = []
        self.calls: list[list[dict]] = []
        self.recorded: list[list[dict]] = []


STATE = _State()


@activity.defn(name="build_prompt")
async def _build_prompt(inp: BuildPromptInput) -> list[dict]:
    return [{"role": "system", "content": "s"}, {"role": "user", "content": inp.message}]


@activity.defn(name="llm_chat")
async def _llm_chat(inp: LLMChatInput) -> LLMResponseData:
    STATE.calls.append(list(inp.messages))
    text = STATE.replies[min(len(STATE.calls) - 1, len(STATE.replies) - 1)]
    return LLMResponseData(content=text, finish_reason="stop", has_tool_calls=False, tool_calls=[])


@activity.defn(name="record_turn")
async def _record_turn(inp: RecordTurnInput) -> None:
    STATE.recorded.append(list(inp.new_messages))


def _note(tools_used: list[str], draft: str) -> str | None:
    return None if "search_products" in tools_used else NOTE


@workflow.defn(name="ContractGateProbeWorkflow")
class _ContractGateProbeWorkflow:
    @workflow.run
    async def run(self, mode: str) -> dict:
        session = SessionInput(
            session_id="wa_gate", channel="whatsapp", chat_id="wa_gate", llm=LLMConfig(model="fake"),
            workspace=WorkspaceConfig(path="/tmp/ws"), tool_definitions_json="[]",
        )
        policy = None
        if mode != "none":
            policy = TurnPolicy(extra_round_note=lambda *_: None, final_round_note=_note)
        result = await run_agent_turn(
            session, PendingMessage(message="¿cuánto vale el Cubo Love?"), episode_id="ep_001",
            admin_turn=mode == "admin", turn_policy=policy,
        )
        return {"final": result.final_content, "guards": [s.get("name") for s in result.steps if s.get("kind") == "guard"]}


async def _turn(mode: str, replies: list[str]) -> dict[str, Any]:
    STATE.__init__()
    STATE.replies = replies
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=QUEUE, workflows=[_ContractGateProbeWorkflow],
            activities=[_build_prompt, _llm_chat, _record_turn], workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            return await env.client.execute_workflow(
                _ContractGateProbeWorkflow.run, mode, id=f"gate-probe-{mode}", task_queue=QUEUE
            )


def _remembered() -> list[str]:
    return [m.get("content") for batch in STATE.recorded for m in batch if m.get("role") == "assistant"]


async def test_a_text_without_the_required_tool_gets_one_more_round_naming_it() -> None:
    out = await _turn("gate", ["La Cubo Love cuesta $45.000 🤍", "Déjame confirmarlo 🤍"])

    assert len(STATE.calls) == 2
    assert STATE.calls[1][-1] == {"role": "system", "content": NOTE}
    assert out["final"] == "Déjame confirmarlo 🤍"
    assert "contract_extra_round" in out["guards"]
    assert "La Cubo Love cuesta $45.000 🤍" not in _remembered(), "el borrador que no salió no se recuerda"


async def test_only_one_extra_round() -> None:
    out = await _turn("gate", ["La Cubo Love cuesta $45.000 🤍", "Cuesta $45.000 🤍"])

    assert len(STATE.calls) == 2 and out["final"] == "Cuesta $45.000 🤍"


async def test_without_the_note_the_turn_is_todays() -> None:
    out = await _turn("none", ["La Cubo Love cuesta $45.000 🤍"])

    assert len(STATE.calls) == 1 and out["final"] == "La Cubo Love cuesta $45.000 🤍"
    assert "contract_extra_round" not in out["guards"]


async def test_an_admin_turn_never_gets_the_extra_round() -> None:
    await _turn("admin", ["Etiqueta INTERESADO."])

    assert len(STATE.calls) == 1
