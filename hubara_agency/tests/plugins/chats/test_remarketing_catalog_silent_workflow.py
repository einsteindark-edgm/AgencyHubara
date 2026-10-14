"""RemarketingWorkflow con el cliente que se calló tras el catálogo (2026-10-13).

* El 1er toque enviado agenda el 2º para la mañana siguiente (decisión del
  operador: a +2h del 1º respondía el 4 %).
* El toque que explica el producto no sale con un número inventado: se
  vuelve a redactar UNA vez con el gancho corto de siempre (el que explica es
  justo el que puede inventar «dura 60 horas», como inventó «viene en vaso»).

Harness: WorkflowEnvironment.start_time_skipping() + activities fake con
tracker (patrón test_remarketing_ladder_workflow.py).
"""
from __future__ import annotations

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from exoclaw_temporal.config import (
    LLMConfig,
    LLMResponseData,
    SessionInput,
    WorkspaceConfig,
)

from src.platform.contracts import RemarketingEligibility
from src.platform.llm_history_reset import ResetLLMHistoryInput
from src.platform.orchestration import EventEnvelope
from src.platform.orchestration.dispatcher import DispatchResult
from src.platform.plugin_manifest import get_task_queue
from src.platform.whatsapp.reengagement_ladder import NEXT_TOUCH_MORNING
from src.platform.whatsapp.send_policy import SendDecision
from src.plugins.chats.agent.remarketing.contracts import (
    RemarketingContext,
    RemarketingSessionInput,
    RemarketingTriggerInput,
)
from src.plugins.chats.agent.remarketing.workflows.remarketing import (
    RemarketingSessionWorkflow,
)

REMARKETING_QUEUE = get_task_queue("chats", "remarketing")
SID = "wa_573001234567"


class Tracker:
    def __init__(self) -> None:
        self.sends: list[str] = []
        self.touches: list[tuple[str, str]] = []
        self.triggers: list[RemarketingTriggerInput] = []
        self.record_turns = 0
        self.llm_calls = 0


def _fakes(tracker: Tracker, *, llm_contents: list[str], workspace_path: str, context: RemarketingContext):
    @activity.defn(name="check_remarketing_eligibility")
    async def fake_eligibility(session_id: str) -> RemarketingEligibility:
        return RemarketingEligibility(eligible=True, current_route="ventas", current_tag="INTERESADO")

    @activity.defn(name="check_reengagement_policy")
    async def fake_policy(session_id: str) -> SendDecision:
        return SendDecision(
            allowed=True, channel="free_form", recommended_category="service",
            is_free=True, expected_cost_micros=0, rationale="ok",
        )

    @activity.defn(name="bootstrap_remarketing_session_activity")
    async def fake_bootstrap(input: RemarketingSessionInput) -> SessionInput:
        return SessionInput(
            session_id=input.session_id, channel="whatsapp", chat_id=input.session_id,
            llm=LLMConfig(model="fake"), workspace=WorkspaceConfig(path=workspace_path),
            tool_definitions_json="[]",
        )

    @activity.defn(name="claim_conversation_routing")
    async def fake_claim(session_id: str, new_route: str) -> None:
        return None

    @activity.defn(name="read_workspace_memory_activity")
    async def fake_memory(session_id: str) -> str:
        return ""

    @activity.defn(name="read_remarketing_context_activity")
    async def fake_context(session_id: str) -> RemarketingContext:
        return context

    @activity.defn(name="build_remarketing_trigger_v2_activity")
    async def fake_trigger_v2(input: RemarketingTriggerInput) -> str:
        tracker.triggers.append(input)
        mode = "explica" if input.silent_after_catalog else "corto"
        return f"[SYSTEM] toque {input.touch_number} {mode}"

    @activity.defn(name="send_typing_indicator_activity")
    async def fake_typing(session_id: str) -> None:
        return None

    @activity.defn(name="build_prompt")
    async def fake_build_prompt(input) -> list:
        return [{"role": "user", "content": getattr(input, "message", "")}]

    @activity.defn(name="llm_chat")
    async def fake_llm(input) -> LLMResponseData:
        content = llm_contents[min(tracker.llm_calls, len(llm_contents) - 1)]
        tracker.llm_calls += 1
        return LLMResponseData(content=content, finish_reason="stop", has_tool_calls=False, tool_calls=[])

    @activity.defn(name="execute_tool")
    async def fake_execute_tool(input) -> str:
        return "ok"

    @activity.defn(name="record_turn")
    async def fake_record_turn(input) -> None:
        tracker.record_turns += 1

    @activity.defn(name="get_active_episode_id")
    async def fake_episode_id(session_id: str) -> str:
        return "ep_001"

    @activity.defn(name="reset_llm_history_for_episode")
    async def fake_reset_llm_history(inp: ResetLLMHistoryInput) -> bool:
        return False

    @activity.defn(name="send_whatsapp_message_activity")
    async def fake_send(session_id: str, message: str) -> None:
        tracker.sends.append(message)

    @activity.defn(name="persist_assistant_message_activity")
    async def fake_persist(session_id: str, message: str) -> None:
        return None

    @activity.defn(name="record_remarketing_touch_activity")
    async def fake_record_touch(session_id: str, kind: str, next_touch: str = "") -> None:
        tracker.touches.append((kind, next_touch))

    @activity.defn(name="write_pending_handoff")
    async def fake_write_handoff(session_id: str, summary: str) -> None:
        return None

    @activity.defn(name="orchestration.dispatch_event")
    async def fake_dispatch_event(envelope: EventEnvelope) -> DispatchResult:
        return DispatchResult(source_plugin="chats", source_worker="remarketing", event_type="x")

    return [
        fake_eligibility, fake_policy, fake_bootstrap, fake_claim, fake_memory, fake_context,
        fake_trigger_v2, fake_typing, fake_build_prompt, fake_llm, fake_execute_tool,
        fake_record_turn, fake_episode_id, fake_reset_llm_history, fake_send, fake_persist,
        fake_record_touch, fake_write_handoff, fake_dispatch_event,
    ]


async def _run(tmp_path, *, llm_contents: list[str], context: RemarketingContext) -> Tracker:
    tracker = Tracker()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=REMARKETING_QUEUE, workflows=[RemarketingSessionWorkflow],
            activities=_fakes(tracker, llm_contents=llm_contents, workspace_path=str(tmp_path), context=context),
        ):
            handle = await env.client.start_workflow(
                RemarketingSessionWorkflow.run,
                RemarketingSessionInput(session_id=SID, motivo="vio la colección"),
                id=f"remarketing-{SID}", task_queue=REMARKETING_QUEUE,
            )
            await handle.result()
    return tracker


def _silent(touch_number: int = 1, allowed: tuple[str, ...] = ("40",)) -> RemarketingContext:
    return RemarketingContext(
        touch_number=touch_number, silence_minutes=130, total_touches=3,
        catalog_facts="- Calabaza: vela de cera de palma, 40 horas.",
        silent_after_catalog=True, catalog_described=True, allowed_numbers=list(allowed),
    )


@pytest.mark.asyncio
async def test_el_primer_toque_agenda_el_segundo_para_la_manana_siguiente(tmp_path) -> None:
    tracker = await _run(tmp_path, llm_contents=["La Calabaza dura 40 horas 🎃 ¿Es para ti o para regalar?"], context=_silent())
    assert tracker.sends == ["La Calabaza dura 40 horas 🎃 ¿Es para ti o para regalar?"]
    assert tracker.touches == [("free_form", NEXT_TOUCH_MORNING)]
    assert tracker.triggers[0].silent_after_catalog is True
    assert tracker.triggers[0].total_touches == 3


@pytest.mark.asyncio
async def test_el_segundo_toque_no_agenda_nada(tmp_path) -> None:
    # Los tiempos vienen de los datos de la tienda: están entre los permitidos.
    tracker = await _run(
        tmp_path, llm_contents=["A Bogotá llega en 1 a 2 días hábiles 🚚"],
        context=_silent(touch_number=2, allowed=("40", "1", "2", "3")),
    )
    assert tracker.sends == ["A Bogotá llega en 1 a 2 días hábiles 🚚"]
    assert tracker.touches == [("free_form", "")]


@pytest.mark.asyncio
async def test_si_la_redaccion_corta_tambien_inventa_no_sale_nada(tmp_path) -> None:
    tracker = await _run(tmp_path, llm_contents=["Dura 60 horas", "Mide 12 cm"], context=_silent())
    assert tracker.sends == []
    assert tracker.touches == [("abstained", "")]


@pytest.mark.asyncio
async def test_quien_converso_sigue_con_el_hueco_normal(tmp_path) -> None:
    tracker = await _run(tmp_path, llm_contents=["Hola de nuevo 🌿"], context=RemarketingContext(touch_number=1, silence_minutes=130))
    assert tracker.touches == [("free_form", "")]


@pytest.mark.asyncio
async def test_un_numero_inventado_no_sale_y_se_redacta_el_gancho_corto(tmp_path) -> None:
    tracker = await _run(
        tmp_path,
        llm_contents=["La Calabaza dura 60 horas 🎃", "¿Alguna de las de Halloween te llamó la atención? 🎃"],
        context=_silent(),
    )
    assert tracker.sends == ["¿Alguna de las de Halloween te llamó la atención? 🎃"]
    assert [t.silent_after_catalog for t in tracker.triggers] == [True, False], "la 2ª redacción es la corta"
    assert tracker.record_turns == 1, "el gancho con el número inventado no entra al historial"
    assert tracker.touches == [("free_form", NEXT_TOUCH_MORNING)]


@pytest.mark.asyncio
async def test_la_respuesta_a_un_mensaje_del_cliente_no_pasa_por_la_guarda_de_numeros(tmp_path) -> None:
    """Revisión de producción del PR #426 (2026-10-14): el cliente escribe
    mientras se arma el 1er toque y el turno pasa a ser SUYO. La respuesta con
    una cifra real («el envío nacional arranca en $16.940»: con ventas-6 no hay
    datos de la tienda en las fuentes) se tomaba por inventada y no salía nada:
    ni respuesta ni relevo. La guarda es del gancho proactivo, no de la charla."""
    import asyncio

    tracker = Tracker()
    entered, gate = asyncio.Event(), asyncio.Event()
    reply = "El envío nacional arranca en $16.940 🚚 ¿A qué ciudad sería?"
    context = _silent()

    @activity.defn(name="read_remarketing_context_activity")
    async def gated_context(session_id: str) -> RemarketingContext:
        entered.set()
        await gate.wait()
        return context

    activities = [
        a for a in _fakes(tracker, llm_contents=[reply], workspace_path=str(tmp_path), context=context)
        if a.__temporal_activity_definition.name != "read_remarketing_context_activity"
    ] + [gated_context]
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue=REMARKETING_QUEUE, workflows=[RemarketingSessionWorkflow],
                          activities=activities):
            handle = await env.client.start_workflow(
                RemarketingSessionWorkflow.run, RemarketingSessionInput(session_id=SID, motivo="vio la colección"),
                id=f"remarketing-{SID}", task_queue=REMARKETING_QUEUE,
            )
            await entered.wait()
            await handle.signal("send_message", args=["hola, ¿cuánto vale el envío a Medellín?", None, None])
            await asyncio.sleep(0.3)
            gate.set()
            await handle.result()

    assert tracker.sends == [reply]
