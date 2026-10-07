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
from temporalio.exceptions import ApplicationError
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
    WorkspaceConfig,
)
from src.platform.observability.cost_attribution import RecordEpisodeLLMUsageInput
from src.platform.workflow_helpers import PendingMessage, run_agent_turn

QUEUE = "test-run-agent-turn-egress"
LEAK = "El cliente pregunta el precio. Le respondo.\n\n¡La Cubo Love cuesta $45.000! 🤍"
ANSWER = "¡La Cubo Love cuesta $45.000! 🤍"


class _State:
    """Lo que el turno de prueba vio y grabó (el worker corre en este proceso)."""

    def __init__(self) -> None:
        self.llm_text = ""
        # Respuestas del LLM en orden (con tools); vacío = `llm_text` sin tools.
        self.responses: list[LLMResponseData] = []
        self.tool_results: dict[str, str] = {}
        self.egress_out: dict[str, Any] | None = None
        self.egress_calls: list[tuple[str, dict]] = []
        self.recorded: list[list[dict]] = []
        # Costo registrado al episodio: (episodio, tokens de entrada, de salida).
        self.usage: list[tuple[str, int, int]] = []


STATE = _State()


@activity.defn(name="build_prompt")
async def _build_prompt(inp: BuildPromptInput) -> list[dict]:
    return [{"role": "system", "content": "s"}, {"role": "user", "content": inp.message}]


@activity.defn(name="llm_chat")
async def _llm_chat(inp: LLMChatInput) -> LLMResponseData:
    if STATE.responses:
        return STATE.responses.pop(0)
    return LLMResponseData(content=STATE.llm_text, finish_reason="stop", has_tool_calls=False, tool_calls=[])


@activity.defn(name="execute_tool")
async def _execute_tool(inp: ExecuteToolInput) -> str:
    return STATE.tool_results.get(inp.name, "{}")


@activity.defn(name="record_turn")
async def _record_turn(inp: RecordTurnInput) -> None:
    STATE.recorded.append(list(inp.new_messages))


@activity.defn(name="probe_egress")
async def _probe_egress(final_text: str, ctx: dict) -> dict:
    STATE.egress_calls.append((final_text, ctx))
    return dict(STATE.egress_out or {})


@activity.defn(name="record_episode_llm_usage")
async def _record_usage(inp: RecordEpisodeLLMUsageInput) -> None:
    STATE.usage.append((inp.episode_id, inp.prompt_tokens, inp.completion_tokens))


@activity.defn(name="record_episode_llm_usage")
async def _usage_that_fails(inp: RecordEpisodeLLMUsageInput) -> None:
    """El registro del costo agotó sus intentos (p. ej. el disco del vault)."""
    raise ApplicationError("no se pudo escribir el costo", non_retryable=True)


@workflow.defn(name="EgressProbeWorkflow")
class _EgressProbeWorkflow:
    def __init__(self) -> None:
        self._new_input = False

    @workflow.run
    async def run(self, mode: str, writes: str = "", opt_in: bool = False) -> dict:
        """`writes`: cuándo escribe el cliente ("" nunca; "egress" mientras
        corre el egreso; "start" desde antes de la primera respuesta del LLM).
        `opt_in`: el turno pasa `interrupt_before_record` (solo el V2)."""
        session = SessionInput(
            session_id="wa_egress",
            channel="whatsapp",
            chat_id="wa_egress",
            llm=LLMConfig(model="fake"),
            workspace=WorkspaceConfig(path="/tmp/ws"),
            tool_definitions_json="[]",
        )
        self._new_input = writes == "start"

        async def hook(final_text: str, ctx: dict) -> dict:
            out = await workflow.execute_activity(
                _probe_egress, args=[final_text, ctx], start_to_close_timeout=timedelta(seconds=10)
            )
            if writes == "egress":
                self._new_input = True
            return out

        result = await run_agent_turn(
            session,
            PendingMessage(message="¿cuánto vale?"),
            episode_id="ep_001",
            has_new_input=(lambda: self._new_input) if writes else None,
            admin_turn=mode == "admin",
            salvage_leaked_text=mode == "v1",
            egress=None if mode == "v1" else hook,
            interrupt_before_record=opt_in,
        )
        return {
            "final_content": result.final_content,
            "egress": result.egress,
            "salvaged_leak": result.salvaged_leak,
            "discarded": list(result.discarded_narration),
            "guards": [s.get("name") for s in result.steps if s.get("kind") == "guard"],
            "sanitizer": [
                {k: s.get(k) for k in ("before", "after", "actions")}
                for s in result.steps if s.get("kind") == "guard" and s.get("name") == "sanitizer"
            ],
            "interrupted": result.interrupted,
            "cuts": [s.get("reason") for s in result.steps if s.get("kind") == "cut"],
        }


async def _turn(
    mode: str = "v2",
    *,
    llm_text: str = "",
    egress_out: dict | None = None,
    responses: list[LLMResponseData] | None = None,
    tool_results: dict[str, str] | None = None,
    writes: str = "",
    opt_in: bool = False,
    usage_fails: bool = False,
) -> dict:
    STATE.__init__()
    STATE.llm_text = llm_text
    STATE.egress_out = egress_out
    STATE.responses = list(responses or [])
    STATE.tool_results = dict(tool_results or {})
    usage = _usage_that_fails if usage_fails else _record_usage
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=QUEUE,
            workflows=[_EgressProbeWorkflow],
            activities=[_build_prompt, _llm_chat, _execute_tool, _record_turn, _probe_egress, usage],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            return await env.client.execute_workflow(
                _EgressProbeWorkflow.run, args=[mode, writes, opt_in], id=f"egress-probe-{mode}", task_queue=QUEUE
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
        # Lo que escribió el LLM antes del saneador: el egreso decide la
        # muletilla de presentación (capacidad `preambulo`).
        "raw_text": LEAK,
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


# ── La muletilla del modelo (capacidad `preambulo`) la decide el egreso ─────
#
# El saneador de la plataforma es mecánico salvo un paso: el meta-prefijo
# («Aquí tienes:»). Con gancho, el egreso recibe lo que el LLM escribió ANTES
# del saneador (`raw_text`) y decide ese paso con el motor; el turno solo
# aplica lo que el egreso decidió. Sin gancho, la regla de hoy (ver arriba).

PREAMBLE = "Aquí tienes:\n¡Hola! ¿Qué aroma te gusta?"
HELLO = "¡Hola! ¿Qué aroma te gusta?"


def _calls(*calls: tuple[str, dict]) -> LLMResponseData:
    from exoclaw_temporal.config import ToolCallData

    return LLMResponseData(
        content="",
        finish_reason="tool_calls",
        has_tool_calls=True,
        tool_calls=[ToolCallData(id=f"c{i}", name=name, arguments=args) for i, (name, args) in enumerate(calls, 1)],
    )


async def test_the_hook_gets_what_the_llm_wrote_before_the_sanitizer() -> None:
    await _turn(llm_text=PREAMBLE, egress_out={"text": HELLO, "llm_text": HELLO})

    [(text, ctx)] = STATE.egress_calls
    assert (text, ctx["raw_text"]) == (HELLO, PREAMBLE)  # el texto de hoy y lo que escribió el LLM


async def test_the_llm_remembers_the_preamble_the_egress_decided_to_keep() -> None:
    """Jev dijo que «Aquí tienes:» le habla al cliente: sale entero, el LLM
    lo recuerda entero y la traza no muestra un saneado que no pasó."""
    sanitizer = {"before": PREAMBLE, "after": PREAMBLE, "actions": []}

    result = await _turn(
        llm_text=PREAMBLE, egress_out={"text": PREAMBLE, "llm_text": PREAMBLE, "sanitizer": sanitizer}
    )

    assert _remembered(STATE.recorded[0]) == [PREAMBLE]
    assert result["final_content"] == PREAMBLE
    assert result["sanitizer"] == []


async def test_the_trace_shows_the_preamble_the_egress_cut() -> None:
    unknown = "Claro, aquí va el mensaje para el cliente:\n\n¡Hola! La Cubo Love cuesta $45.000 🤍"
    answer = "¡Hola! La Cubo Love cuesta $45.000 🤍"
    sanitizer = {"before": unknown, "after": answer, "actions": ["meta_prefix_stripped"]}

    result = await _turn(llm_text=unknown, egress_out={"text": answer, "llm_text": answer, "sanitizer": sanitizer})

    assert _remembered(STATE.recorded[0]) == [answer]
    assert result["sanitizer"] == [sanitizer]


async def test_the_handoff_farewell_goes_to_the_egress_before_the_sanitizer() -> None:
    farewell = "Aquí va:\nUn colega del equipo te responde en este mismo chat 🤍"
    envelope = (
        '{"escalation_decision": {"session_id": "wa_egress", "reason_category": "BULK_ORDER", "summary": "s"}, '
        '"customer_message": "Aquí va:\\nUn colega del equipo te responde en este mismo chat 🤍"}'
    )

    await _turn(
        responses=[_calls(("escalate_to_human", {"reason_category": "BULK_ORDER"}))],
        tool_results={"escalate_to_human": envelope},
        egress_out={"text": "x", "llm_text": "x"},
    )

    [(text, ctx)] = STATE.egress_calls
    assert (text, ctx["raw_text"]) == ("Un colega del equipo te responde en este mismo chat 🤍", farewell)


async def test_a_text_a_tool_already_validated_is_not_sanitized_again() -> None:
    """`send_reply` ya limpió su texto con el motor: el egreso no lo relee."""
    await _turn(
        responses=[_calls(("send_reply", {"text": HELLO}))],
        tool_results={"send_reply": '{"reply": {"text": "¡Hola! ¿Qué aroma te gusta?"}}'},
        egress_out={"text": HELLO, "llm_text": HELLO},
    )

    [(text, ctx)] = STATE.egress_calls
    assert text == HELLO and ctx.get("raw_text") is None


# ── Revisión antes de grabar y enviar (ráfagas sin cortes, 2026-10-06) ─────
#
# El cliente escribe mientras corre el egreso (hasta siete preguntas a Jev):
# hasta grabar el turno nada salió (ni el texto de `send_reply`, que lo envía
# el workflow después). Con `interrupt_before_record` (solo el V2) el turno se
# corta ahí, como en el Checkpoint A: no se graba y el caller lo recompone.

_ORDER = (
    '{"registered": true, "order_registered": {"session_id": "wa_egress", "order_id": "order_9", '
    '"payment_method": "transfer", "total_cop": 45000, "currency": "COP", "motivo": "pedido"}}'
)
_USAGE = {"prompt_tokens": 1200, "completion_tokens": 30}


def _said(text: str, *, usage: dict | None = None) -> LLMResponseData:
    return LLMResponseData(content=text, finish_reason="stop", has_tool_calls=False, tool_calls=[], usage=usage)


async def test_a_message_during_the_egress_cuts_the_turn_before_recording_it() -> None:
    result = await _turn(llm_text=ANSWER, egress_out={"text": ANSWER, "llm_text": ANSWER}, writes="egress", opt_in=True)

    assert result["interrupted"] is True
    assert result["final_content"] == ""
    assert result["cuts"] == ["before_record"]
    assert STATE.recorded == []  # el turno nunca pasó: el LLM no lo recuerda


async def test_without_the_opt_in_the_turn_is_recorded_as_today() -> None:
    """V1, remarketing y ETA no pasan `interrupt_before_record`: ni un comando
    distinto (el mensaje va al turno siguiente)."""
    result = await _turn(llm_text=ANSWER, egress_out={"text": ANSWER, "llm_text": ANSWER}, writes="egress")

    assert result["interrupted"] is False and result["cuts"] == []
    assert _remembered(STATE.recorded[0]) == [ANSWER]


async def test_a_send_reply_has_not_reached_the_customer_yet_so_the_turn_is_cut() -> None:
    """`send_reply` no envía nada: su texto lo manda el workflow después."""
    result = await _turn(
        responses=[_calls(("send_reply", {"text": HELLO}))],
        tool_results={"send_reply": '{"reply": {"text": "¡Hola! ¿Qué aroma te gusta?"}}'},
        egress_out={"text": HELLO, "llm_text": HELLO},
        writes="egress",
        opt_in=True,
    )

    assert result["interrupted"] is True and result["cuts"] == ["send_reply", "before_record"]
    assert STATE.recorded == []


async def test_a_turn_that_already_showed_something_is_not_cut() -> None:
    """La ficha del producto ya va camino al cliente: no hay reinicio limpio."""
    result = await _turn(
        responses=[_calls(("present_product_detail", {"handle": "cubo-love"})), _said(ANSWER)],
        tool_results={"present_product_detail": '{"queued": true}'},
        egress_out={"text": ANSWER, "llm_text": ANSWER},
        writes="egress",
        opt_in=True,
    )

    assert result["interrupted"] is False and "before_record" not in result["cuts"]
    assert len(STATE.recorded) == 1


async def test_a_turn_that_registered_an_order_is_not_cut() -> None:
    result = await _turn(
        responses=[_calls(("register_order", {"confirmado": True})), _said("Listo, tu pedido quedó registrado 🤍")],
        tool_results={"register_order": _ORDER},
        egress_out={"text": "Listo, tu pedido quedó registrado 🤍", "llm_text": "Listo, tu pedido quedó registrado 🤍"},
        writes="egress",
        opt_in=True,
    )

    assert result["interrupted"] is False
    assert len(STATE.recorded) == 1


async def test_every_cut_attempt_records_its_cost() -> None:
    """Un intento cortado también le costó al episodio (antes se perdía)."""
    cut = await _turn(
        responses=[_said(ANSWER, usage=_USAGE)], egress_out={"text": ANSWER, "llm_text": ANSWER},
        writes="egress", opt_in=True,
    )
    assert cut["interrupted"] is True and STATE.usage == [("ep_001", 1200, 30)]

    early = await _turn(responses=[_said(ANSWER, usage=_USAGE)], writes="start", opt_in=True)
    assert early["cuts"] == ["checkpoint_a"] and STATE.usage == [("ep_001", 1200, 30)]


async def test_without_the_opt_in_a_cut_attempt_records_nothing_as_today() -> None:
    early = await _turn(responses=[_said(ANSWER, usage=_USAGE)], writes="start")

    assert early["cuts"] == ["checkpoint_a"] and STATE.usage == []


async def test_a_cut_whose_cost_cannot_be_recorded_still_restarts() -> None:
    """El costo de un intento cortado es contabilidad: si su activity agota
    los intentos, el turno igual se corta y el caller lo recompone. Antes la
    excepción salía de `run_agent_turn` y tumbaba la sesión del V2 (revisión
    del PR #391)."""
    cut = await _turn(
        responses=[_said(ANSWER, usage=_USAGE)], egress_out={"text": ANSWER, "llm_text": ANSWER},
        writes="egress", opt_in=True, usage_fails=True,
    )
    assert (cut["interrupted"], cut["cuts"]) == (True, ["before_record"])

    early = await _turn(responses=[_said(ANSWER, usage=_USAGE)], writes="start", opt_in=True, usage_fails=True)
    assert (early["interrupted"], early["cuts"]) == (True, ["checkpoint_a"])


async def test_a_reaction_already_queued_for_the_customer_blocks_the_cut() -> None:
    """`react_to_message` no tiene prefijo de salida, pero deja la reacción
    encolada (`queued: true`): el flush la envía aunque el turno se corte y el
    reintento reaccionaría otra vez (revisión del PR #391). Lo que una tool
    dejó encolado para el cliente cuenta como algo que le llegó."""
    result = await _turn(
        responses=[_calls(("react_to_message", {"emoji": "🤍"})), _said(ANSWER)],
        tool_results={"react_to_message": '{"queued": true, "kind": "reaction", "emoji": "🤍"}'},
        egress_out={"text": ANSWER, "llm_text": ANSWER},
        writes="egress",
        opt_in=True,
    )

    assert result["interrupted"] is False and "before_record" not in result["cuts"]
    assert len(STATE.recorded) == 1


async def test_a_tool_that_queued_nothing_does_not_block_the_cut() -> None:
    """Control: una tool interna sin nada encolado (`queued: false` o sin
    sobre) no le mostró nada al cliente; el corte sigue."""
    result = await _turn(
        responses=[_calls(("set_order_slot", {"direccion": "Carrera 7 # 12-34"})), _said(ANSWER)],
        tool_results={"set_order_slot": '{"updated": true, "queued": false}'},
        egress_out={"text": ANSWER, "llm_text": ANSWER},
        writes="egress",
        opt_in=True,
    )

    assert result["interrupted"] is True and result["cuts"] == ["before_record"]
