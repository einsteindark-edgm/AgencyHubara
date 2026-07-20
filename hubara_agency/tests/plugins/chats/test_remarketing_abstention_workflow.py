"""Workflow-level test del canal de abstención NO_MESSAGE del Remarketing.

Incidente wa_573229041190 (2026-07-17, run 019f7234): el trigger de
reactivación llegó cuando ya no correspondía; el LLM decidió "no genero un
nuevo mensaje" pero el workflow enviaba TODO `final_content` no vacío — la
deliberación interna le llegó al cliente por WhatsApp.

Contrato nuevo (gated `workflow.patched("no-message-abstention-v1")`):
cuando el LLM responde el sentinel `NO_MESSAGE` (sin transfer), el workflow
NO envía, NO persiste, devuelve el routing a ventas y termina.

Harness: WorkflowEnvironment.start_time_skipping() + activities fake con
tracker (patrón test_remarketing_policy_gate_workflow.py).
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
        self.claim_calls: list[str] = []
        self.send_whatsapp_calls: list[str] = []
        self.persist_calls: list[str] = []


def _make_fake_activities(
    tracker: Tracker, *, llm_content: str, workspace_path: str
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
            channel="free_form",
            recommended_category="service",
            is_free=True,
            expected_cost_micros=0,
            rationale="CSW abierta",
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

    @activity.defn(name="send_typing_indicator_activity")
    async def fake_typing(session_id: str) -> None:
        return None

    @activity.defn(name="build_prompt")
    async def fake_build_prompt(input) -> list:
        return [{"role": "user", "content": getattr(input, "message", "")}]

    @activity.defn(name="llm_chat")
    async def fake_llm(input) -> LLMResponseData:
        return LLMResponseData(
            content=llm_content,
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
        tracker.persist_calls.append(message)

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


async def _run_workflow(tracker: Tracker, llm_content: str, tmp_path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=REMARKETING_QUEUE,
            workflows=[RemarketingSessionWorkflow],
            activities=_make_fake_activities(
                tracker, llm_content=llm_content, workspace_path=str(workspace)
            ),
        ):
            handle = await env.client.start_workflow(
                RemarketingSessionWorkflow.run,
                RemarketingSessionInput(
                    session_id="wa_abstention_test", motivo="cliente interesado"
                ),
                id="remarketing-wa_abstention_test",
                task_queue=REMARKETING_QUEUE,
            )
            await handle.result()


@pytest.mark.asyncio
async def test_no_message_sentinel_suppresses_send_and_releases_routing(
    tmp_path,
) -> None:
    tracker = Tracker()
    await _run_workflow(tracker, "NO_MESSAGE", tmp_path)

    assert tracker.send_whatsapp_calls == [], (
        "abstención → NINGÚN mensaje al cliente"
    )
    assert tracker.persist_calls == [], (
        "abstención → nada en el historial (no contaminar el turno de Sales)"
    )
    # claim a remarketing en bootstrap + claim de vuelta a ventas al abstenerse.
    assert tracker.claim_calls == ["remarketing", "ventas"], (
        "abstención → devolver el routing a ventas y terminar"
    )


@pytest.mark.asyncio
async def test_sentinel_with_leaked_explanation_still_suppresses(tmp_path) -> None:
    # El modelo desobedece el "sin explicación" — la intención sigue clara y
    # NADA de ese texto debe llegar al cliente.
    tracker = Tracker()
    await _run_workflow(
        tracker, "NO_MESSAGE\n\nEl cliente ya respondió al gancho.", tmp_path
    )
    assert tracker.send_whatsapp_calls == []
    assert tracker.claim_calls == ["remarketing", "ventas"]


@pytest.mark.asyncio
async def test_normal_hook_still_sends(tmp_path) -> None:
    # Regresión: un gancho normal sigue enviándose exactamente 1 vez.
    tracker = Tracker()
    await _run_workflow(tracker, "¡Hola! ¿Seguimos con tu pedido? 🤍", tmp_path)
    assert tracker.send_whatsapp_calls == ["¡Hola! ¿Seguimos con tu pedido? 🤍"]
