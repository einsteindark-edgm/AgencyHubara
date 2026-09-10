"""Workflow-level: el gancho recibe el contexto REAL de la conversación.

Incidente run dc32f7fe (2026-09-10, wa_573114842180): el ciclo del Window
Strategist arrancó `RemarketingWorkflow` con motivo="Window Strategist:
reactivación (csw_free_form)" y el trigger salió sin historial ni motivo del
tag → "quedó pendiente lo de tu pedido" a un cliente que quería comprar cera.

Contrato nuevo (gated `workflow.patched("remarketing-context-v1")`): el
workflow lee `read_remarketing_context_activity` y construye el trigger con
`build_remarketing_trigger_v2_activity` usando el motivo del TAG (si existe),
el flag de draft y el transcript. La activity legacy no se invoca.

Harness: patrón test_remarketing_abstention_workflow.py.
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
STRATEGIST_MOTIVO = "Window Strategist: reactivación (csw_free_form)"
TAG_MOTIVO = "buscaba cera, no la vendemos"
TRANSCRIPT = "Cliente: Precio de la cera\nAsesor: La cera no se vende aparte\nCliente: Gracias"


class Tracker:
    def __init__(self) -> None:
        self.trigger_v2_inputs: list[RemarketingTriggerInput] = []
        self.legacy_trigger_calls: list[str] = []
        self.prompts_seen: list[str] = []
        self.send_whatsapp_calls: list[str] = []


def _make_fake_activities(tracker: Tracker, *, context: RemarketingContext, workspace_path: str):
    @activity.defn(name="check_remarketing_eligibility")
    async def fake_eligibility(session_id: str) -> RemarketingEligibility:
        return RemarketingEligibility(eligible=True, current_route="ventas", current_tag="INTERESADO")

    @activity.defn(name="check_reengagement_policy")
    async def fake_policy(session_id: str) -> SendDecision:
        return SendDecision(
            allowed=True, channel="free_form", recommended_category="service",
            is_free=True, expected_cost_micros=0, rationale="CSW abierta",
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

    @activity.defn(name="build_remarketing_trigger_activity")
    async def fake_trigger_legacy(motivo: str, memory_context: str) -> str:
        tracker.legacy_trigger_calls.append(motivo)
        return f"[LEGACY] {motivo}"

    @activity.defn(name="build_remarketing_trigger_v2_activity")
    async def fake_trigger_v2(input: RemarketingTriggerInput) -> str:
        tracker.trigger_v2_inputs.append(input)
        return f"[V2] {input.motivo} | draft={input.has_order_draft} | {input.transcript}"

    @activity.defn(name="send_typing_indicator_activity")
    async def fake_typing(session_id: str) -> None:
        return None

    @activity.defn(name="build_prompt")
    async def fake_build_prompt(input) -> list:
        # Sin type hint Temporal entrega el payload como dict.
        msg = input.get("message", "") if isinstance(input, dict) else getattr(input, "message", "")
        tracker.prompts_seen.append(msg)
        return [{"role": "user", "content": msg}]

    @activity.defn(name="llm_chat")
    async def fake_llm(input) -> LLMResponseData:
        return LLMResponseData(content="NO_MESSAGE", finish_reason="stop", has_tool_calls=False, tool_calls=[])

    @activity.defn(name="execute_tool")
    async def fake_execute_tool(input) -> str:
        return "ok"

    @activity.defn(name="record_turn")
    async def fake_record_turn(input) -> None:
        return None

    @activity.defn(name="get_active_episode_id")
    async def fake_episode_id(session_id: str) -> str:
        return "ep_001"

    @activity.defn(name="send_whatsapp_message_activity")
    async def fake_send(session_id: str, message: str) -> None:
        tracker.send_whatsapp_calls.append(message)

    @activity.defn(name="persist_assistant_message_activity")
    async def fake_persist(session_id: str, message: str) -> None:
        return None

    return [
        fake_eligibility, fake_policy, fake_bootstrap, fake_claim, fake_memory,
        fake_context, fake_trigger_legacy, fake_trigger_v2, fake_typing,
        fake_build_prompt, fake_llm, fake_execute_tool, fake_record_turn,
        fake_episode_id, fake_send, fake_persist,
    ]


async def _run_workflow(tracker: Tracker, context: RemarketingContext, tmp_path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=REMARKETING_QUEUE, workflows=[RemarketingSessionWorkflow],
            activities=_make_fake_activities(tracker, context=context, workspace_path=str(workspace)),
        ):
            handle = await env.client.start_workflow(
                RemarketingSessionWorkflow.run,
                RemarketingSessionInput(session_id="wa_context_test", motivo=STRATEGIST_MOTIVO),
                id="remarketing-wa_context_test", task_queue=REMARKETING_QUEUE,
            )
            await handle.result()


@pytest.mark.asyncio
async def test_trigger_uses_tag_motivo_draft_flag_and_transcript(tmp_path) -> None:
    tracker = Tracker()
    ctx = RemarketingContext(tag_motivo=TAG_MOTIVO, has_order_draft=False, transcript=TRANSCRIPT)
    await _run_workflow(tracker, ctx, tmp_path)

    assert tracker.legacy_trigger_calls == [], "la activity legacy no se invoca bajo el patch"
    (inp,) = tracker.trigger_v2_inputs
    assert inp.motivo == TAG_MOTIVO, "el motivo del TAG manda sobre el string del strategist"
    assert inp.has_order_draft is False
    assert inp.transcript == TRANSCRIPT
    assert tracker.prompts_seen and tracker.prompts_seen[0].startswith("[V2] buscaba cera")
    assert tracker.send_whatsapp_calls == []  # NO_MESSAGE → abstención intacta


@pytest.mark.asyncio
async def test_trigger_falls_back_to_input_motivo_when_tag_has_none(tmp_path) -> None:
    tracker = Tracker()
    await _run_workflow(tracker, RemarketingContext(), tmp_path)
    (inp,) = tracker.trigger_v2_inputs
    assert inp.motivo == STRATEGIST_MOTIVO
    assert inp.transcript == ""
