"""Lo que un intento cortado ya leyó no se vuelve a consultar.

Turno 1 de …7392 (2026-10-08): el modelo buscó la colección de Halloween
(`search_products`), el cliente escribió («¿qué viene incluido?») antes de que
saliera la respuesta y el turno volvió a empezar desde cero: la búsqueda se
perdió y el modelo la repitió (una ronda más, ~2 s y ~29 mil tokens).

Ahora el intento cortado devuelve sus LECTURAS (las tools que el caller dice
que solo leen, con su resultado) y el reinicio las recibe: el modelo las ve
como ya hechas, cuentan como usadas para el contrato del turno, quedan en el
historial del turno y en la traza. Lo que muestra o cambia algo no se
conserva: el mensaje nuevo puede cambiar qué hay que hacer.
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

QUEUE = "test-run-agent-turn-carry-reads"
READS = frozenset({"search_products"})
FOUND = json.dumps({"query": "halloween", "count": 1, "results": [{"handle": "calabaza", "price": "16000"}]})
NOTE = "[CONTRATO DEL TURNO] Antes de responder: busca en el catálogo."


class _State:
    def __init__(self) -> None:
        self.replies: list[Any] = []
        self.calls: list[list[dict]] = []
        self.executed: list[str] = []
        self.recorded: list[list[dict]] = []
        # Llamada del modelo durante la cual «escribe el cliente» (0 = nunca).
        self.writes_on_call = 0


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
    STATE.executed.append(inp.name)
    if inp.name == "search_products":
        return FOUND
    if inp.name == "get_product_by_handle":
        return json.dumps({"error": "catalog_unavailable"})
    return json.dumps({"updated": True})


@activity.defn(name="record_turn")
async def _record_turn(inp: RecordTurnInput) -> None:
    STATE.recorded.append(list(inp.new_messages))


def _note(tools_used: list[str], _draft: str) -> str | None:
    return None if "search_products" in tools_used else NOTE


# Un error del helper falla el workflow (sin esto, la tarea se reintenta y el test cuelga).
@workflow.defn(name="CarryReadsProbeWorkflow", failure_exception_types=[Exception])
class _CarryReadsProbeWorkflow:
    def __init__(self) -> None:
        self._llm_calls_seen = 0

    @workflow.run
    async def run(self, mode: str) -> dict:
        session = SessionInput(
            session_id="wa_carry", channel="whatsapp", chat_id="wa_carry", llm=LLMConfig(model="fake"),
            workspace=WorkspaceConfig(path="/tmp/ws"), tool_definitions_json="[]",
        )
        policy = TurnPolicy(extra_round_note=lambda *_: None, final_round_note=_note)
        msg = PendingMessage(message="Quiero más información de la colección de Halloween")
        first = await run_agent_turn(
            session, msg, episode_id="ep_001", turn_policy=policy,
            has_new_input=lambda: STATE.writes_on_call > 0 and len(STATE.calls) >= STATE.writes_on_call,
            read_only_tools=READS if mode != "no_reads" else frozenset(),
        )
        out: dict[str, Any] = {
            "interrupted": first.interrupted,
            "carried": None if first.carried is None else {
                "messages": list(first.carried.messages), "events": list(first.carried.events),
            },
        }
        if first.interrupted:
            STATE.writes_on_call = 0
            second = await run_agent_turn(
                session, PendingMessage(message=f"{msg.message}\nQue viene incluido"), episode_id="ep_001",
                turn_policy=policy, read_only_tools=READS,
                carried=first.carried if mode != "drop" else None,
            )
            out.update(
                final=second.final_content,
                tools_used=list(second.tools_used),
                events=[e.get("name") for e in second.tool_events],
                steps=[{k: v for k, v in s.items() if k in ("kind", "tools", "name")} for s in second.steps],
            )
        return out


async def _turn(mode: str, replies: list[Any], *, writes_on_call: int) -> dict[str, Any]:
    STATE.__init__()
    STATE.replies = replies
    STATE.writes_on_call = writes_on_call
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=QUEUE, workflows=[_CarryReadsProbeWorkflow],
            activities=[_build_prompt, _llm_chat, _execute_tool, _record_turn],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            return await env.client.execute_workflow(
                _CarryReadsProbeWorkflow.run, mode, id=f"carry-probe-{mode}", task_queue=QUEUE
            )


def _tool_names(messages: list[dict]) -> list[str]:
    return [
        (call.get("function") or {}).get("name")
        for m in messages
        if m.get("role") == "assistant"
        for call in m.get("tool_calls") or []
    ]


_SEARCH = [("search_products", {"q": "halloween"})]
_THE_CASE = [
    _SEARCH,  # intento 1, ronda 1: busca
    [("present_products", {"handles": ["calabaza"], "intro_text": "Mira 🎃"})],  # ronda 2: el cliente escribe
    "La Calabaza viene con aroma a frutos rojos 🎃",  # reinicio: responde
]


async def test_the_cut_attempt_hands_back_what_it_read() -> None:
    out = await _turn("carry", _THE_CASE, writes_on_call=2)

    assert out["interrupted"] is True
    carried = out["carried"]
    assert _tool_names(carried["messages"]) == ["search_products"]
    assert [m for m in carried["messages"] if m.get("role") == "tool"][0]["content"] == FOUND
    assert [e["name"] for e in carried["events"]] == ["search_products"]


async def test_the_restart_sees_the_reads_as_done_and_does_not_repeat_them() -> None:
    out = await _turn("carry", _THE_CASE, writes_on_call=2)

    restart_first_round = STATE.calls[2]
    assert _tool_names(restart_first_round) == ["search_products"]
    assert {"role": "tool", "tool_call_id": "c1_0", "name": "search_products", "content": FOUND} in restart_first_round
    assert STATE.executed == ["search_products"], "la búsqueda corre una sola vez"
    # Cuenta como usada: el contrato (pide search_products) no retiene la respuesta.
    assert out["final"] == "La Calabaza viene con aroma a frutos rojos 🎃"
    assert "search_products" in out["tools_used"] and out["events"] == ["search_products"]
    assert out["steps"][0] == {"kind": "carry", "tools": ["search_products"]}
    # El historial del turno la recuerda (el modelo la usó para responder).
    assert "search_products" in _tool_names(STATE.recorded[-1])


async def test_what_shows_or_changes_something_is_not_carried() -> None:
    out = await _turn(
        "carry",
        [
            [("search_products", {"q": "halloween"}), ("set_order_slot", {"producto": "Calabaza"})],
            [("present_products", {"handles": ["calabaza"], "intro_text": "Mira 🎃"})],
            "Listo 🎃",
        ],
        writes_on_call=2,
    )

    assert _tool_names(out["carried"]["messages"]) == ["search_products"]
    assert [m["name"] for m in out["carried"]["messages"] if m.get("role") == "tool"] == ["search_products"]


async def test_a_read_that_failed_is_not_carried() -> None:
    out = await _turn(
        "carry",
        [[("get_product_by_handle", {"handle": "calabaza"})], [("search_products", {"q": "x"})], "Listo"],
        writes_on_call=1,
    )

    assert out["carried"] is None


async def test_without_the_read_only_list_nothing_is_carried() -> None:
    out = await _turn("no_reads", _THE_CASE, writes_on_call=2)

    assert out["interrupted"] is True and out["carried"] is None


async def test_without_carried_reads_the_restart_is_todays() -> None:
    out = await _turn("drop", [*_THE_CASE[:2], _SEARCH, "La Calabaza 🎃"], writes_on_call=2)

    assert STATE.executed == ["search_products", "search_products"]
    assert not any(s["kind"] == "carry" for s in out["steps"])
