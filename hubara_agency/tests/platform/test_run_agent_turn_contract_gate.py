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
from src.platform.workflow_helpers import PendingMessage, TurnPolicy, run_agent_turn

QUEUE = "test-run-agent-turn-contract-gate"
NOTE = "[CONTRATO DEL TURNO] Antes de responder: el precio sale del catálogo, usa search_products."


class _State:
    def __init__(self) -> None:
        # Cada respuesta del modelo: un texto, o una lista de tool calls
        # `(nombre, argumentos)`.
        self.replies: list[Any] = []
        self.calls: list[list[dict]] = []
        self.recorded: list[list[dict]] = []


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
    """Como las tools reales: `send_reply` devuelve el texto validado y las
    tarjetas quedan encoladas para el cliente."""
    if inp.name == "send_reply":
        return json.dumps({"reply": {"text": inp.params.get("text", "")},
                           "summary": "Mensaje listo para el cliente. Tu turno termina aquí: espera su respuesta."})
    if inp.name == "search_products":
        return json.dumps({"query": inp.params.get("q"), "count": 1,
                           "results": [{"handle": "cubo-love", "title": "Cubo Love", "price": 49500}]})
    if inp.name == "manage_conversation_tag":
        return json.dumps({"tag_closure": {"ends_turn": True, "customer_message": None}})
    return json.dumps({"queued": True})


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
        return {
            "final": result.final_content,
            "guards": [s.get("name") for s in result.steps if s.get("kind") == "guard"],
            "outbound": list(result.outbound_tool_texts),
        }


async def _turn(mode: str, replies: list[str]) -> dict[str, Any]:
    STATE.__init__()
    STATE.replies = replies
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=QUEUE, workflows=[_ContractGateProbeWorkflow],
            activities=[_build_prompt, _llm_chat, _execute_tool, _record_turn],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            return await env.client.execute_workflow(
                _ContractGateProbeWorkflow.run, mode, id=f"gate-probe-{mode}", task_queue=QUEUE
            )


def _remembered() -> list[str]:
    return [m.get("content") for batch in STATE.recorded for m in batch if m.get("role") == "assistant"]


def _remembered_replies() -> list[str]:
    """Los textos de `send_reply` que el historial del LLM recuerda."""
    return [
        json.loads(call["function"]["arguments"]).get("text")
        for batch in STATE.recorded
        for m in batch
        if m.get("role") == "assistant"
        for call in m.get("tool_calls") or []
        if (call.get("function") or {}).get("name") == "send_reply"
    ]


def _send_reply(text: str) -> list[tuple[str, dict]]:
    return [("send_reply", {"text": text})]


async def test_a_text_without_the_required_tool_gets_one_more_round_naming_it() -> None:
    out = await _turn("gate", ["La Cubo Love cuesta $45.000 🤍", "Déjame confirmarlo 🤍"])

    assert len(STATE.calls) == 2
    # Turno 1 de …7392 (2026-10-08): la nota no decía que el texto no salió y
    # el modelo volvió a escribir el mismo; ahora lo dice, como con send_reply.
    assert STATE.calls[1][-1] == {"role": "system", "content": f"Tu respuesta NO se envió. {NOTE}"}
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


# ── La puerta también cuando el modelo cierra con `send_reply` ────────────
# Caso 6543 del laboratorio (caso-fotos-0929-r4, turno 2 del bot nuevo): el
# plan pedía consultar el catálogo y el modelo cerró de una con `send_reply`
# («esa pieza como tal no la manejamos»). La puerta solo miraba el cierre con
# texto suelto: en r4, 12 de 43 turnos del bot nuevo cerraron con
# `send_reply` sin la tool que pedía el contrato.


async def test_a_send_reply_without_the_required_tool_is_held_back_for_one_more_round() -> None:
    out = await _turn("gate", [
        _send_reply("La Cubo Love cuesta $45.000 🤍"),
        [("search_products", {"q": "cubo love"})],
        _send_reply("La Cubo Love cuesta $49.500 🤍"),
    ])

    assert len(STATE.calls) == 3
    assert STATE.calls[1][-1] == {"role": "system", "content": f"Tu send_reply NO se envió. {NOTE}"}
    assert out["final"] == "La Cubo Love cuesta $49.500 🤍"
    assert "contract_extra_round" in out["guards"]


async def test_the_held_back_reply_is_not_remembered_nor_counted_as_sent() -> None:
    """Ni el historial ni los textos que «le llegaron» al cliente (de ahí sale,
    por ejemplo, si ya se le saludó) cuentan una respuesta que no salió."""
    out = await _turn("gate", [
        _send_reply("¡Hola! La Cubo Love cuesta $45.000 🤍"),
        [("search_products", {"q": "cubo love"})],
        _send_reply("Cuesta $49.500 🤍"),
    ])

    assert _remembered_replies() == ["Cuesta $49.500 🤍"]
    assert "¡Hola! La Cubo Love cuesta $45.000 🤍" not in out["outbound"]


async def test_a_send_reply_with_the_required_tool_goes_out() -> None:
    out = await _turn("gate", [[("search_products", {"q": "cubo love"}), ("send_reply", {"text": "Cuesta $49.500 🤍"})]])

    assert len(STATE.calls) == 1 and out["final"] == "Cuesta $49.500 🤍"
    assert "contract_extra_round" not in out["guards"]


async def test_a_send_reply_next_to_a_card_the_customer_will_see_is_not_held_back() -> None:
    """La puerta actúa solo si el cliente todavía no vio nada: una tarjeta ya
    encolada no se puede retirar, y la respuesta la acompaña."""
    out = await _turn("gate", [[
        ("present_products", {"handles": ["cubo-love"], "intro_text": "Mira estas 🤍"}),
        ("send_reply", {"text": "¿Cuál te gusta?"}),
    ]])

    assert len(STATE.calls) == 1 and out["final"] == "¿Cuál te gusta?"
    assert "contract_extra_round" not in out["guards"]


async def test_a_send_reply_next_to_a_form_the_customer_will_see_is_not_held_back() -> None:
    """Una tarjeta que espera al cliente cuenta aunque no lleve texto propio
    (el formulario de envío, las tarifas)."""
    out = await _turn("gate", [[
        ("request_shipping_details", {"items": [{"handle": "cubo-love", "quantity": 1}]}),
        ("send_reply", {"text": "Déjame tus datos en el formulario 🤍"}),
    ]])

    assert len(STATE.calls) == 1 and out["final"] == "Déjame tus datos en el formulario 🤍"


async def test_a_send_reply_that_closes_with_a_tag_is_not_held_back() -> None:
    out = await _turn("gate", [[("manage_conversation_tag", {"tag": "RECHAZO"}), ("send_reply", {"text": "Con gusto 🤍"})]])

    assert len(STATE.calls) == 1 and out["final"] == "Con gusto 🤍"


async def test_only_one_extra_round_when_closing_with_send_reply() -> None:
    out = await _turn("gate", [_send_reply("Cuesta $45.000 🤍"), _send_reply("Cuesta $45.000, te confirmo 🤍")])

    assert len(STATE.calls) == 2 and out["final"] == "Cuesta $45.000, te confirmo 🤍"


async def test_without_the_note_a_send_reply_is_todays() -> None:
    out = await _turn("none", [_send_reply("La Cubo Love cuesta $45.000 🤍")])

    assert len(STATE.calls) == 1 and out["final"] == "La Cubo Love cuesta $45.000 🤍"
    assert "contract_extra_round" not in out["guards"]
