"""Workflow-level test del gate `check_reengagement_policy` (WS-B2).

El RemarketingSessionWorkflow debe consultar la central ANTES de tocar al
cliente: si `decide_reengagement` suprime (humano / convertido / fase B fría /
quiet hours), el workflow returna early SIN side-effects — no claim de routing,
no bootstrap, no mensajes. Si permite, el flujo sigue como siempre.

Con esto la afirmación "hubara re-valida cada envío con la central" pasa a ser
VERDAD para TODOS los dispatchers (agente Window Strategist, dashboard,
transition INTERESADO) — cierra el ⏳ del §9-bis de WHATSAPP_WINDOW_STRATEGY.md.

Harness: WorkflowEnvironment.start_time_skipping() + activities fake con
tracker (patrón test_sales_workflow_debounce.py).
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
from src.plugins.chats.agent.remarketing.contracts import RemarketingSessionInput
from src.plugins.chats.agent.remarketing.workflows.remarketing import (
    RemarketingSessionWorkflow,
)

REMARKETING_QUEUE = get_task_queue("chats", "remarketing")


class Tracker:
    def __init__(self) -> None:
        self.bootstrap_calls: int = 0
        self.claim_calls: list[str] = []
        self.send_whatsapp_calls: list[str] = []
        self.policy_calls: int = 0


def _allowed_decision() -> SendDecision:
    return SendDecision(
        allowed=True,
        channel="free_form",
        recommended_category="service",
        is_free=True,
        expected_cost_micros=0,
        rationale="CSW abierta",
    )


def _suppressed_decision() -> SendDecision:
    return SendDecision(
        allowed=False,
        channel="blocked",
        recommended_category="marketing",
        is_free=False,
        expected_cost_micros=0,
        rationale="fase B: lead frío",
        suppress_reason="fase_b_cold_suppressed",
    )


def _make_fake_activities(
    tracker: Tracker, *, decision: SendDecision, workspace_path: str
):
    @activity.defn(name="check_remarketing_eligibility")
    async def fake_eligibility(session_id: str) -> RemarketingEligibility:
        return RemarketingEligibility(
            eligible=True, current_route="ventas", current_tag="INTERESADO"
        )

    @activity.defn(name="check_reengagement_policy")
    async def fake_policy(session_id: str) -> SendDecision:
        tracker.policy_calls += 1
        return decision

    @activity.defn(name="bootstrap_remarketing_session_activity")
    async def fake_bootstrap(input: RemarketingSessionInput) -> SessionInput:
        tracker.bootstrap_calls += 1
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

    @activity.defn(name="send_typing_indicator_activity")
    async def fake_typing(session_id: str) -> None:
        return None

    @activity.defn(name="build_prompt")
    async def fake_build_prompt(input) -> list:
        return [{"role": "user", "content": getattr(input, "message", "")}]

    @activity.defn(name="llm_chat")
    async def fake_llm(input) -> LLMResponseData:
        return LLMResponseData(
            content="¡Hola! ¿Seguimos con tu pedido?",
            finish_reason="stop",
            has_tool_calls=False,
            tool_calls=[],
        )

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
        fake_eligibility,
        fake_policy,
        fake_bootstrap,
        fake_claim,
        fake_memory,
        fake_trigger,
        fake_typing,
        fake_build_prompt,
        fake_llm,
        fake_execute_tool,
        fake_record_turn,
        fake_episode_id,
        fake_send,
        fake_persist,
    ]


async def _run_workflow(tracker: Tracker, decision: SendDecision, tmp_path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=REMARKETING_QUEUE,
            workflows=[RemarketingSessionWorkflow],
            activities=_make_fake_activities(
                tracker, decision=decision, workspace_path=str(workspace)
            ),
        ):
            handle = await env.client.start_workflow(
                RemarketingSessionWorkflow.run,
                RemarketingSessionInput(
                    session_id="wa_gate_test", motivo="cliente interesado"
                ),
                id="remarketing-wa_gate_test",
                task_queue=REMARKETING_QUEUE,
            )
            await handle.result()


@pytest.mark.asyncio
async def test_gate_suppress_aborts_without_side_effects(tmp_path) -> None:
    tracker = Tracker()
    await _run_workflow(tracker, _suppressed_decision(), tmp_path)

    assert tracker.policy_calls == 1, "el workflow debe consultar la central"
    assert tracker.bootstrap_calls == 0, (
        "gate suprimió — el workflow NO debe bootstrapear la sesión"
    )
    assert tracker.claim_calls == [], (
        "gate suprimió — NO debe pisar el routing"
    )
    assert tracker.send_whatsapp_calls == [], (
        "gate suprimió — NINGÚN mensaje al cliente"
    )


@pytest.mark.asyncio
async def test_gate_allowed_proceeds_with_turn(tmp_path) -> None:
    tracker = Tracker()
    await _run_workflow(tracker, _allowed_decision(), tmp_path)

    assert tracker.policy_calls == 1
    assert tracker.bootstrap_calls == 1
    assert "remarketing" in tracker.claim_calls[0]
    assert len(tracker.send_whatsapp_calls) == 1, (
        "decisión allowed → el turno del trigger manda exactamente 1 mensaje"
    )
