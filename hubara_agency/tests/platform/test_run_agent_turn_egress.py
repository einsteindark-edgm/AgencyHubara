"""El gancho de egreso de `run_agent_turn` (motor de decisiones, F4).

Desde el run 28a8e407 el texto final se rescata ANTES de grabarlo en el
historial del LLM: el LLM recuerda lo que de verdad salió. El workflow de
ventas V2 no tiene reglas de texto: le pasa al turno un gancho (`egress`) que
pide los veredictos del egreso al motor (la activity `decide_egress`). El
gancho corre donde corría el rescate, lo REEMPLAZA y su resultado viaja en
`TurnResult.egress`; el historial guarda el texto que el egreso decidió
enviar, y nada si decidió no enviar.

Sin gancho (el default) el turno es el de siempre: V1, remarketing y ETA no
cambian ni un comando (sus historias re-juegan igual; ver
`tests/test_replay_sales.py`).
"""
from __future__ import annotations

from datetime import timedelta
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
from src.platform.workflow_helpers import PendingMessage, run_agent_turn

QUEUE = "test-run-agent-turn-egress"
LEAK = "El cliente pregunta el precio. Le respondo.\n\n¡La Cubo Love cuesta $45.000! 🤍"
ANSWER = "¡La Cubo Love cuesta $45.000! 🤍"


class _State:
    """Lo que el turno de prueba vio y grabó (el worker corre en este proceso)."""

    def __init__(self) -> None:
        self.llm_text = ""
        self.egress_out: dict[str, Any] | None = None
        self.egress_calls: list[tuple[str, dict]] = []
        self.recorded: list[list[dict]] = []


STATE = _State()


@activity.defn(name="build_prompt")
async def _build_prompt(inp: BuildPromptInput) -> list[dict]:
    return [{"role": "system", "content": "s"}, {"role": "user", "content": inp.message}]


@activity.defn(name="llm_chat")
async def _llm_chat(inp: LLMChatInput) -> LLMResponseData:
    return LLMResponseData(content=STATE.llm_text, finish_reason="stop", has_tool_calls=False, tool_calls=[])


@activity.defn(name="record_turn")
async def _record_turn(inp: RecordTurnInput) -> None:
    STATE.recorded.append(list(inp.new_messages))


@activity.defn(name="probe_egress")
async def _probe_egress(final_text: str, ctx: dict) -> dict:
    STATE.egress_calls.append((final_text, ctx))
    return dict(STATE.egress_out or {})


@workflow.defn(name="EgressProbeWorkflow")
class _EgressProbeWorkflow:
    @workflow.run
    async def run(self, mode: str) -> dict:
        session = SessionInput(
            session_id="wa_egress",
            channel="whatsapp",
            chat_id="wa_egress",
            llm=LLMConfig(model="fake"),
            workspace=WorkspaceConfig(path="/tmp/ws"),
            tool_definitions_json="[]",
        )

        async def hook(final_text: str, ctx: dict) -> dict:
            return await workflow.execute_activity(
                _probe_egress, args=[final_text, ctx], start_to_close_timeout=timedelta(seconds=10)
            )

        result = await run_agent_turn(
            session,
            PendingMessage(message="¿cuánto vale?"),
            episode_id="ep_001",
            admin_turn=mode == "admin",
            salvage_leaked_text=mode == "v1",
            egress=None if mode == "v1" else hook,
        )
        return {
            "final_content": result.final_content,
            "egress": result.egress,
            "salvaged_leak": result.salvaged_leak,
            "discarded": list(result.discarded_narration),
            "guards": [s.get("name") for s in result.steps if s.get("kind") == "guard"],
        }


async def _turn(mode: str = "v2", *, llm_text: str, egress_out: dict | None = None) -> dict:
    STATE.__init__()
    STATE.llm_text = llm_text
    STATE.egress_out = egress_out
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=QUEUE,
            workflows=[_EgressProbeWorkflow],
            activities=[_build_prompt, _llm_chat, _record_turn, _probe_egress],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            return await env.client.execute_workflow(
                _EgressProbeWorkflow.run, mode, id=f"egress-probe-{mode}", task_queue=QUEUE
            )


def _remembered(recorded: list[dict]) -> list[str]:
    return [m.get("content") for m in recorded if m.get("role") == "assistant" and not m.get("tool_calls")]


async def test_the_hook_decides_what_the_llm_remembers_and_travels_in_the_result() -> None:
    out = {"text": ANSWER, "llm_text": ANSWER, "rescued_before_record": True, "greeting_needed": False}

    result = await _turn(llm_text=LEAK, egress_out=out)

    [(text, ctx)] = STATE.egress_calls
    assert text == LEAK
    assert ctx == {
        "first_contact": True,
        "tools_used": [],
        "outbound_tool_texts": [],
        "order_registered": False,
        "portavelas_included": None,
        "admin_turn": False,
    }
    assert result["egress"] == out
    assert result["final_content"] == ANSWER
    assert _remembered(STATE.recorded[0]) == [ANSWER]
    # El rescate antes de grabar deja el mismo rastro que el del V1.
    assert result["salvaged_leak"] is True and result["discarded"] == [LEAK]
    assert "salvage_leak" in result["guards"]


async def test_a_rescue_that_keeps_every_paragraph_leaves_the_same_trace_as_v1() -> None:
    """Como en la rama de siempre: si hubo rescate, queda su rastro aunque no
    haya caído ningún párrafo (un patrón que cruza párrafos)."""
    same = "Hola 🤍\n\n¿Qué aroma te gustaría?"

    result = await _turn(llm_text=same, egress_out={"text": same, "llm_text": same, "rescued_before_record": True})

    assert result["salvaged_leak"] is True and result["discarded"] == [same]
    assert "salvage_leak" in result["guards"]


async def test_what_the_hook_does_not_send_is_not_remembered() -> None:
    blocked = "Etiqueta registrada."

    result = await _turn(llm_text=blocked, egress_out={"text": "", "llm_text": blocked, "blocked": True})

    assert _remembered(STATE.recorded[0]) == []
    assert result["final_content"] == blocked and result["salvaged_leak"] is False


async def test_a_text_the_egress_changes_is_remembered_as_it_goes_out() -> None:
    farewell = "¡Listo! Tu pedido quedó registrado 🤍 Los colores del portavelas se escogen al pagar."
    sent = "¡Listo! Tu pedido quedó registrado 🤍"

    result = await _turn(llm_text=farewell, egress_out={"text": sent, "llm_text": farewell, "portavelas": True})

    assert _remembered(STATE.recorded[0]) == [sent]
    assert result["final_content"] == farewell  # lo que el LLM escribió (la traza lo muestra)
    assert result["guards"] == []


async def test_the_abstention_sentinel_is_still_remembered() -> None:
    """`NO_MESSAGE` es el canal correcto de abstención: verlo usado es el
    few-shot bueno (igual que en el V1), aunque no salga nada."""
    await _turn(llm_text="NO_MESSAGE", egress_out={"text": "", "llm_text": "NO_MESSAGE", "blocked": True})

    assert _remembered(STATE.recorded[0]) == ["NO_MESSAGE"]


async def test_an_admin_turn_is_never_remembered() -> None:
    await _turn("admin", llm_text="Etiquetada como INTERESADO.", egress_out={"text": "", "llm_text": "Etiquetada como INTERESADO."})

    assert STATE.recorded[0] == []
    [(_text, ctx)] = STATE.egress_calls
    assert ctx["admin_turn"] is True


async def test_without_the_hook_the_turn_is_todays() -> None:
    result = await _turn("v1", llm_text=LEAK)

    assert STATE.egress_calls == []
    assert result["egress"] is None
    assert result["final_content"] == ANSWER and result["salvaged_leak"] is True
    assert _remembered(STATE.recorded[0]) == [ANSWER]
