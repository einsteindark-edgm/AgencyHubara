"""RemarketingWorkflow bajo la escalera de reactivación (decisión 2026-09-18).

Incidente runs `01a0b0da`…`01a0b586` (86% de los runs morían en <30s):
  * la abstención no dejaba rastro determinista → re-despacho cada 45 min;
  * cada `NO_MESSAGE` se grababa en el historial del LLM → auto-refuerzo;
  * con `policy.channel == "template"` el workflow igual corría el LLM
    free-form (Meta rechazaría el envío fuera de la ventana de 24h);
  * un workflow vivo (esperando respuesta 24h) descartaba los intents
    siguientes (`via: start_workflow`) → imposible un segundo toque.

Harness: WorkflowEnvironment.start_time_skipping() + activities fake con
tracker (patrón test_remarketing_abstention_workflow.py).
"""
from __future__ import annotations

import asyncio

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
from src.platform.orchestration import EventEnvelope
from src.platform.orchestration.dispatcher import DispatchResult
from src.platform.plugin_manifest import get_task_queue
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
        self.claim_calls: list[str] = []
        self.sends: list[str] = []
        self.touches: list[str] = []
        self.record_turns: int = 0
        self.llm_calls: int = 0
        self.template_calls: int = 0
        self.trigger_touch_numbers: list[int | None] = []
        self.prompts: list[str] = []
        self.handoffs: list[str] = []
        self.context_gate: asyncio.Event | None = None


def _fakes(
    tracker: Tracker,
    *,
    llm_contents: list[str],
    workspace_path: str,
    channel: str = "free_form",
    template_result: str = "sent",
):
    @activity.defn(name="check_remarketing_eligibility")
    async def fake_eligibility(session_id: str) -> RemarketingEligibility:
        return RemarketingEligibility(
            eligible=True, current_route="ventas", current_tag="INTERESADO"
        )

    @activity.defn(name="check_reengagement_policy")
    async def fake_policy(session_id: str) -> SendDecision:
        return SendDecision(
            allowed=True,
            channel=channel,
            recommended_category="service" if channel == "free_form" else "marketing",
            is_free=True,
            expected_cost_micros=0,
            rationale="ok",
        )

    @activity.defn(name="bootstrap_remarketing_session_activity")
    async def fake_bootstrap(input: RemarketingSessionInput) -> SessionInput:
        return SessionInput(
            session_id=input.session_id,
            channel="whatsapp",
            chat_id=input.session_id,
            llm=LLMConfig(model="fake"),
            workspace=WorkspaceConfig(path=workspace_path),
            tool_definitions_json="[]",
        )

    @activity.defn(name="claim_conversation_routing")
    async def fake_claim(session_id: str, new_route: str) -> None:
        tracker.claim_calls.append(new_route)

    @activity.defn(name="read_workspace_memory_activity")
    async def fake_memory(session_id: str) -> str:
        return ""

    @activity.defn(name="build_remarketing_trigger_activity")
    async def fake_trigger(motivo: str, memory_context: str) -> str:
        return f"[SYSTEM] reactivar: {motivo}"

    @activity.defn(name="read_remarketing_context_activity")
    async def fake_context(session_id: str) -> RemarketingContext:
        if tracker.context_gate is not None and tracker.touches:
            # Carrera (H-1): el followup queda "a mitad de camino" hasta que
            # el test suelte la compuerta — mientras, el cliente escribe.
            await tracker.context_gate.wait()
        # El nº de toque sale del estado real de la escalera (acá: los toques
        # ya registrados por el propio workflow).
        return RemarketingContext(
            touch_number=len(tracker.touches) + 1, silence_minutes=125
        )

    @activity.defn(name="build_remarketing_trigger_v2_activity")
    async def fake_trigger_v2(input: RemarketingTriggerInput) -> str:
        tracker.trigger_touch_numbers.append(input.touch_number)
        return f"[SYSTEM] reactivar toque {input.touch_number}"

    @activity.defn(name="send_typing_indicator_activity")
    async def fake_typing(session_id: str) -> None:
        return None

    @activity.defn(name="build_prompt")
    async def fake_build_prompt(input) -> list:
        tracker.prompts.append(str(getattr(input, "message", "")))
        return [{"role": "user", "content": getattr(input, "message", "")}]

    @activity.defn(name="llm_chat")
    async def fake_llm(input) -> LLMResponseData:
        content = llm_contents[min(tracker.llm_calls, len(llm_contents) - 1)]
        tracker.llm_calls += 1
        return LLMResponseData(
            content=content, finish_reason="stop", has_tool_calls=False, tool_calls=[]
        )

    @activity.defn(name="execute_tool")
    async def fake_execute_tool(input) -> str:
        return "ok"

    @activity.defn(name="record_turn")
    async def fake_record_turn(input) -> None:
        tracker.record_turns += 1

    @activity.defn(name="get_active_episode_id")
    async def fake_episode_id(session_id: str) -> str:
        return "ep_001"

    @activity.defn(name="send_whatsapp_message_activity")
    async def fake_send(session_id: str, message: str) -> None:
        tracker.sends.append(message)

    @activity.defn(name="persist_assistant_message_activity")
    async def fake_persist(session_id: str, message: str) -> None:
        return None

    @activity.defn(name="record_remarketing_touch_activity")
    async def fake_record_touch(session_id: str, kind: str) -> None:
        tracker.touches.append(kind)

    @activity.defn(name="send_remarketing_template_activity")
    async def fake_template(session_id: str, is_free: bool) -> str:
        tracker.template_calls += 1
        return template_result

    @activity.defn(name="write_pending_handoff")
    async def fake_write_handoff(session_id: str, summary: str) -> None:
        tracker.handoffs.append(summary)

    @activity.defn(name="orchestration.dispatch_event")
    async def fake_dispatch_event(envelope: EventEnvelope) -> DispatchResult:
        return DispatchResult(
            source_plugin="chats", source_worker="remarketing", event_type="x"
        )

    return [
        fake_write_handoff, fake_dispatch_event,
        fake_eligibility, fake_policy, fake_bootstrap, fake_claim, fake_memory,
        fake_trigger, fake_context, fake_trigger_v2, fake_typing,
        fake_build_prompt, fake_llm, fake_execute_tool, fake_record_turn,
        fake_episode_id, fake_send, fake_persist, fake_record_touch,
        fake_template,
    ]


async def _wait_for(predicate, timeout_s: float = 20.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while not predicate():
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("timeout esperando la condición del test")
        await asyncio.sleep(0.05)


def _input() -> RemarketingSessionInput:
    return RemarketingSessionInput(session_id=SID, motivo="cliente interesado")


@pytest.mark.asyncio
async def test_gancho_enviado_registra_el_toque(tmp_path) -> None:
    tracker = Tracker()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=REMARKETING_QUEUE,
            workflows=[RemarketingSessionWorkflow],
            activities=_fakes(tracker, llm_contents=["¡Hola de nuevo! 🌿"],
                              workspace_path=str(tmp_path)),
        ):
            handle = await env.client.start_workflow(
                RemarketingSessionWorkflow.run, _input(),
                id=f"remarketing-{SID}", task_queue=REMARKETING_QUEUE,
            )
            await handle.result()
    assert tracker.sends == ["¡Hola de nuevo! 🌿"]
    assert tracker.touches == ["free_form"]
    assert tracker.trigger_touch_numbers == [1]


@pytest.mark.asyncio
async def test_abstencion_consume_el_peldano_y_no_ensucia_el_historial(tmp_path) -> None:
    tracker = Tracker()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=REMARKETING_QUEUE,
            workflows=[RemarketingSessionWorkflow],
            activities=_fakes(tracker, llm_contents=["NO_MESSAGE"],
                              workspace_path=str(tmp_path)),
        ):
            handle = await env.client.start_workflow(
                RemarketingSessionWorkflow.run, _input(),
                id=f"remarketing-{SID}", task_queue=REMARKETING_QUEUE,
            )
            await handle.result()
    assert tracker.sends == []
    assert tracker.touches == ["abstained"], (
        "sin rastro determinista el ciclo re-despacha cada 45 min (01a0b0da)"
    )
    assert tracker.record_turns == 0, (
        "un NO_MESSAGE grabado es precedente para el siguiente intento"
    )


@pytest.mark.asyncio
async def test_canal_plantilla_envia_plantilla_y_no_corre_el_llm(tmp_path) -> None:
    tracker = Tracker()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=REMARKETING_QUEUE,
            workflows=[RemarketingSessionWorkflow],
            activities=_fakes(tracker, llm_contents=["no debería usarse"],
                              workspace_path=str(tmp_path), channel="template"),
        ):
            handle = await env.client.start_workflow(
                RemarketingSessionWorkflow.run, _input(),
                id=f"remarketing-{SID}", task_queue=REMARKETING_QUEUE,
            )
            await handle.result()
    assert tracker.template_calls == 1
    assert tracker.llm_calls == 0
    assert tracker.sends == []
    assert tracker.touches == ["template"]
    # Queda atento a la respuesta del cliente y al vencer devuelve el routing.
    assert tracker.claim_calls == ["remarketing", "ventas"]


@pytest.mark.asyncio
async def test_plantilla_fallida_consume_el_peldano_y_libera_el_routing(tmp_path) -> None:
    tracker = Tracker()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=REMARKETING_QUEUE,
            workflows=[RemarketingSessionWorkflow],
            activities=_fakes(tracker, llm_contents=["x"], workspace_path=str(tmp_path),
                              channel="template", template_result="failed:131049"),
        ):
            handle = await env.client.start_workflow(
                RemarketingSessionWorkflow.run, _input(),
                id=f"remarketing-{SID}", task_queue=REMARKETING_QUEUE,
            )
            await handle.result()
    assert tracker.touches == ["failed"]
    assert tracker.claim_calls[-1] == "ventas"


@pytest.mark.asyncio
async def test_next_touch_con_workflow_vivo_envia_el_segundo_gancho(tmp_path) -> None:
    tracker = Tracker()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=REMARKETING_QUEUE,
            workflows=[RemarketingSessionWorkflow],
            activities=_fakes(
                tracker,
                llm_contents=["Primer gancho 🌿", "Otro ángulo: ¿te ayudo a elegir?"],
                workspace_path=str(tmp_path),
            ),
        ):
            handle = await env.client.start_workflow(
                RemarketingSessionWorkflow.run, _input(),
                id=f"remarketing-{SID}", task_queue=REMARKETING_QUEUE,
            )
            await _wait_for(lambda: len(tracker.touches) == 1)
            await handle.signal("next_touch", {"session_id": SID, "motivo": "ws"})
            await _wait_for(lambda: len(tracker.touches) == 2)
            await handle.result()
    assert tracker.sends == ["Primer gancho 🌿", "Otro ángulo: ¿te ayudo a elegir?"]
    assert tracker.touches == ["free_form", "free_form"]
    assert tracker.trigger_touch_numbers == [1, 2]


@pytest.mark.asyncio
async def test_signal_with_start_en_frio_no_duplica_el_primer_toque(tmp_path) -> None:
    tracker = Tracker()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=REMARKETING_QUEUE,
            workflows=[RemarketingSessionWorkflow],
            activities=_fakes(tracker, llm_contents=["Único gancho 🌿"],
                              workspace_path=str(tmp_path)),
        ):
            handle = await env.client.start_workflow(
                RemarketingSessionWorkflow.run, _input(),
                id=f"remarketing-{SID}", task_queue=REMARKETING_QUEUE,
                start_signal="next_touch",
                start_signal_args=[{"session_id": SID, "motivo": "ws"}],
            )
            await handle.result()
    assert tracker.sends == ["Único gancho 🌿"]
    assert tracker.touches == ["free_form"]


@pytest.mark.asyncio
async def test_cliente_escribe_mientras_se_arma_el_toque_siguiente(tmp_path) -> None:
    """Carrera (hallazgo H-1 de la revisión): el cliente escribe MIENTRAS
    `_followup_touch` corre sus activities → `_pending == [cliente, trigger]`.
    Gana el cliente: su mensaje llega a Sales y el trigger interno jamás viaja
    como si fuera texto del usuario."""
    tracker = Tracker()
    tracker.context_gate = asyncio.Event()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=REMARKETING_QUEUE,
            workflows=[RemarketingSessionWorkflow],
            activities=_fakes(tracker, llm_contents=["Primer gancho 🌿", "ok"],
                              workspace_path=str(tmp_path)),
        ):
            handle = await env.client.start_workflow(
                RemarketingSessionWorkflow.run, _input(),
                id=f"remarketing-{SID}", task_queue=REMARKETING_QUEUE,
            )
            await _wait_for(lambda: len(tracker.touches) == 1)
            await handle.signal("next_touch", {"session_id": SID, "motivo": "ws"})
            await asyncio.sleep(0.5)  # el followup ya está esperando la compuerta
            await handle.signal("send_message", args=["Hola, sí me interesa la calabaza", None, None])
            tracker.context_gate.set()
            await handle.result()
    assert len(tracker.sends) == 1, "no sale un segundo gancho encima del cliente"
    assert tracker.touches == ["free_form"], "el toque 2 no se consumió"
    assert tracker.handoffs and "calabaza" in tracker.handoffs[-1]
    assert all("SYSTEM" not in h for h in tracker.handoffs), (
        "el trigger interno viajó a Sales como texto del cliente"
    )
