"""El egreso puede retener el texto con una nota (puerta de afirmaciones, 2026-11-03).

Colossus (el bot V2) afirma hechos que nunca consultó: «tu pedido quedó
registrado», «ya recibimos tu pago», «te llega mañana». El egreso del V2 corre
antes de grabar el turno; ahora puede devolver `retain_note` («retener, con
esta nota») y `run_agent_turn` le da al modelo UNA ronda más, con la misma
forma que la segunda puerta y la ronda de lo prometido: el texto retenido no
se graba ni se recuerda, el modelo sabe que no salió y el texto nuevo vuelve
a pasar por el egreso. Una segunda nota no retiene otra vez (nunca un bucle):
el texto sale. Sin nota, el turno es el de siempre (sin comandos nuevos: el
parche `egress-retain-round-v1` solo se consulta cuando hay nota).
"""
from __future__ import annotations

import json
from datetime import timedelta
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
from src.platform.workflow_helpers import EGRESS_RETAIN_PATCH, PendingMessage, run_agent_turn

QUEUE = "test-run-agent-turn-egress-retain"
CLAIM = "¡Listo! Tu pedido quedó registrado 🤍"
FIXED = "Ya casi: para registrar tu pedido me faltan tus datos de envío 🤍"
NOTE = "Dijiste que el pedido quedó registrado y no hay ningún pedido registrado."


class _State:
    def __init__(self) -> None:
        self.replies: list[Any] = []
        self.calls: list[list[dict]] = []
        self.tools: list[str] = []
        # Lo que devuelve el egreso en cada llamada (la última se repite).
        self.egress_outs: list[dict[str, Any]] = []
        self.egress_calls: list[tuple[str, dict]] = []
        self.recorded: list[dict] = []


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
    STATE.tools.append(inp.name)
    if inp.name == "send_reply":
        return json.dumps({"reply": {"text": inp.params.get("text", "")}, "summary": "Mensaje listo para el cliente."})
    if inp.name == "check_order_status":
        return json.dumps({"orders": [{"order_id": "o_1", "pay_status": "paid", "payment_confirmed": True}]})
    return json.dumps({"ok": True})


@activity.defn(name="record_turn")
async def _record_turn(inp: RecordTurnInput) -> None:
    STATE.recorded = list(inp.new_messages)


@activity.defn(name="probe_egress")
async def _probe_egress(final_text: str, ctx: dict) -> dict:
    STATE.egress_calls.append((final_text, ctx))
    out = STATE.egress_outs[min(len(STATE.egress_calls) - 1, len(STATE.egress_outs) - 1)]
    return {"text": final_text, "llm_text": final_text, "final_text": final_text, **out}


@workflow.defn(name="EgressRetainProbeWorkflow")
class _EgressRetainProbeWorkflow:
    @workflow.run
    async def run(self, max_iterations: int = 10, admin: bool = False) -> dict:
        session = SessionInput(
            session_id="wa_retain", channel="whatsapp", chat_id="wa_retain",
            llm=LLMConfig(model="fake", max_iterations=max_iterations),
            workspace=WorkspaceConfig(path="/tmp/ws"), tool_definitions_json="[]",
        )

        async def hook(final_text: str, ctx: dict) -> dict:
            return await workflow.execute_activity(
                _probe_egress, args=[final_text, ctx], start_to_close_timeout=timedelta(seconds=10)
            )

        result = await run_agent_turn(
            session, PendingMessage(message="¿Ya quedó mi pedido?"), episode_id="ep_001", egress=hook,
            admin_turn=admin,
        )
        return {
            "final": result.final_content,
            "egress": result.egress,
            "guards": [s.get("name") for s in result.steps if s.get("kind") == "guard"],
            "retained": [
                {k: s.get(k) for k in ("before", "after")}
                for s in result.steps if s.get("kind") == "guard" and s.get("name") == "egress_retain_round"
            ],
        }


async def _turn(
    replies: list[Any], egress_outs: list[dict[str, Any]], *, max_iterations: int = 10, admin: bool = False
) -> dict[str, Any]:
    STATE.__init__()
    STATE.replies = replies
    STATE.egress_outs = egress_outs
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=QUEUE, workflows=[_EgressRetainProbeWorkflow],
            activities=[_build_prompt, _llm_chat, _execute_tool, _record_turn, _probe_egress],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                _EgressRetainProbeWorkflow.run, args=[max_iterations, admin], id="egress-retain", task_queue=QUEUE
            )
            out = await handle.result()
            # Los marcadores de parche que grabó el turno (el id viaja en sus detalles).
            markers = " ".join([
                str(e.marker_recorded_event_attributes)
                async for e in handle.fetch_history_events()
                if e.HasField("marker_recorded_event_attributes")
            ])
            return {**out, "patches": markers}


def _system_notes() -> list[str]:
    """Las notas de sistema que vio el modelo (cada una una vez: los mensajes se acumulan por ronda)."""
    notes = [m["content"] for call in STATE.calls for m in call if m.get("role") == "system" and m["content"] != "s"]
    return list(dict.fromkeys(notes))


def _remembered() -> list[str]:
    texts = [m.get("content") for m in STATE.recorded if m.get("role") == "assistant" and not m.get("tool_calls")]
    replies = [
        json.loads(m.get("content") or "{}").get("reply", {}).get("text")
        for m in STATE.recorded if m.get("role") == "tool"
    ]
    return [t for t in [*texts, *replies] if t]


async def test_a_loose_text_the_egress_retains_gets_one_more_round_with_its_note() -> None:
    out = await _turn([CLAIM, FIXED], [{"retain_note": NOTE}, {}])

    assert [text for text, _ctx in STATE.egress_calls] == [CLAIM, FIXED]
    assert len(STATE.calls) == 2
    # El modelo sabe que su texto no salió, y por qué.
    assert _system_notes() == [f"Tu respuesta NO se envió. {NOTE}"]
    # Lo retenido no está en lo que ve la segunda ronda ni en lo que se graba.
    assert all(m.get("content") != CLAIM for m in STATE.calls[1])
    assert _remembered() == [FIXED]
    assert out["final"] == FIXED
    assert out["egress"]["text"] == FIXED
    assert out["retained"] == [{"before": CLAIM, "after": NOTE}]


async def test_without_a_note_the_turn_is_the_one_of_today() -> None:
    """El caso legítimo (gotcha 16): un texto que el egreso deja pasar sale en
    la primera ronda, sin ronda extra y sin consultar el parche."""
    out = await _turn([FIXED], [{}])

    assert len(STATE.calls) == 1 and len(STATE.egress_calls) == 1
    assert out["final"] == FIXED and "egress_retain_round" not in out["guards"]
    assert _remembered() == [FIXED]
    assert EGRESS_RETAIN_PATCH not in out["patches"]


async def test_the_retention_round_is_behind_its_patch() -> None:
    out = await _turn([CLAIM, FIXED], [{"retain_note": NOTE}, {}])

    assert EGRESS_RETAIN_PATCH in out["patches"]


async def test_a_second_note_never_retains_again() -> None:
    """Una retención por turno: si el modelo insiste, el texto sale (lo que
    salía antes de la puerta)."""
    out = await _turn([CLAIM, CLAIM, FIXED], [{"retain_note": NOTE}])

    assert len(STATE.calls) == 2 and len(STATE.egress_calls) == 2
    assert out["final"] == CLAIM
    assert out["guards"].count("egress_retain_round") == 1


async def test_an_admin_turn_is_never_retained() -> None:
    """El texto de un turno administrativo nunca le llega al cliente: no hay
    nada que retener."""
    out = await _turn([CLAIM, FIXED], [{"retain_note": NOTE}], admin=True)

    assert len(STATE.calls) == 1
    assert "egress_retain_round" not in out["guards"]


async def test_without_a_round_left_the_text_goes_out() -> None:
    """Sin otra vuelta del modelo (la última ronda del turno), el texto sale
    como antes de la puerta: nunca deja al cliente sin respuesta."""
    out = await _turn([CLAIM, FIXED], [{"retain_note": NOTE}], max_iterations=1)

    assert len(STATE.calls) == 1
    assert out["final"] == CLAIM and "egress_retain_round" not in out["guards"]


async def test_a_send_reply_the_egress_retains_is_not_remembered() -> None:
    """La misma puerta para el texto de `send_reply`: el retenido no se graba
    (su llamada sale del historial) y el modelo responde de nuevo."""
    out = await _turn(
        [
            [("send_reply", {"text": CLAIM})],
            [("check_order_status", {})],
            [("send_reply", {"text": FIXED})],
        ],
        [{"retain_note": NOTE}, {}],
    )

    assert STATE.tools == ["send_reply", "check_order_status", "send_reply"]
    assert [text for text, _ctx in STATE.egress_calls] == [CLAIM, FIXED]
    assert _system_notes() == [f"Tu respuesta NO se envió. {NOTE}"]
    assert out["final"] == FIXED
    assert _remembered() == [FIXED]


async def test_the_egress_reads_the_results_of_the_turn_tools() -> None:
    """Los hechos de la puerta salen de lo que el turno consultó: el egreso
    recibe cada tool con su resultado (lo que ya viaja en la traza)."""
    await _turn([[("check_order_status", {})], FIXED], [{}])

    [(_text, ctx)] = STATE.egress_calls
    [event] = ctx["tool_events"]
    assert event["name"] == "check_order_status"
    assert json.loads(event["result"])["orders"][0]["payment_confirmed"] is True
