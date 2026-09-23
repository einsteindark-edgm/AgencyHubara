"""La bienvenida de marca es para el primer contacto, no para quien vuelve.

Fase 3 (run 28a8e407): cada episodio nuevo arranca con el historial del LLM
cortado, así que "no hay mensajes del agente en el historial" ya no significa
"cliente nuevo". Sin esto, al cliente que compró ayer y vuelve hoy le llegaba
"¡Buenas tardes! Bienvenido a *Hubara*, velas artesanales…" otra vez (pasó el
15:02 con la respuesta a la campaña). Cliente que vuelve = el episodio activo
no es el primero de la sesión.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from exoclaw_temporal.config import LLMResponseData, ToolCallData
from src.plugins.chats.agent.sales.contracts import SalesSessionInput
from src.plugins.chats.agent.sales.workflows.sales_session import HubaraSalesSessionWorkflow
from tests.test_sales_workflow_debounce import (
    FIRST_CONTACT_GREETING,
    SALES_QUEUE,
    Tracker,
    _make_fake_activities,
)


async def _first_turn_of_episode(tracker: Tracker, tmp_path: Path, episode_id: str) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()

    @activity.defn(name="get_active_episode_id")
    async def episode(session_id: str) -> str:
        return episode_id

    activities = [
        a
        for a in _make_fake_activities(
            tracker,
            workspace_path=str(workspace),
            llm_responses=[
                LLMResponseData(
                    content="",
                    finish_reason="tool_calls",
                    has_tool_calls=True,
                    tool_calls=[
                        ToolCallData(
                            id="p1",
                            name="present_products",
                            arguments={"handles": ["cubo-love"], "intro_text": "Estas son:"},
                        )
                    ],
                )
            ],
            tool_results={"present_products": json.dumps({"queued": True})},
        )
        if getattr(a, "__temporal_activity_definition").name != "get_active_episode_id"
    ]
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflow],
            activities=[*activities, episode],
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflow.run,
                SalesSessionInput(session_id="wa_back", runtime_workspace_path=str(workspace)),
                id="session-wa_back",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(
                HubaraSalesSessionWorkflow.send_message, args=["Muéstrame", None, None]
            )
            await handle.result()


@pytest.mark.asyncio
async def test_returning_customer_gets_no_brand_welcome(tmp_path: Path) -> None:
    tracker = Tracker()
    await _first_turn_of_episode(tracker, tmp_path, "ep_007")

    sent = [m for (_s, m) in tracker.send_whatsapp_calls]
    assert FIRST_CONTACT_GREETING not in sent
    assert tracker.first_contact_greeting_calls == 0


@pytest.mark.asyncio
async def test_brand_new_customer_still_gets_it(tmp_path: Path) -> None:
    tracker = Tracker()
    await _first_turn_of_episode(tracker, tmp_path, "ep_001")

    assert tracker.first_contact_greeting_calls == 1
