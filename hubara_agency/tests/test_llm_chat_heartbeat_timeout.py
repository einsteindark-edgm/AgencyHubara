"""El turno agenda `llm_chat` con `heartbeat_timeout` (auditoría de Temporal, 2026-10-15).

`llm_chat` late mientras el modelo piensa (`tests/platform/test_llm_chat_heartbeat.py`);
el turno le pone un `heartbeat_timeout` corto: si el worker muere a mitad de la
llamada (deploy, caja sin memoria), Temporal la reintenta en otro worker en
segundos, no al vencer el `start_to_close` de 5 minutos. Solo opciones de la
activity: replay seguro.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from temporalio.api.enums.v1 import EventType
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.chats.agent.sales.contracts import SalesSessionInput
from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2
from tests.test_sales_workflow_debounce import SALES_QUEUE, Tracker, _make_fake_activities


@pytest.mark.asyncio
async def test_the_turn_schedules_llm_chat_with_a_short_heartbeat_timeout(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=SALES_QUEUE,
            workflows=[HubaraSalesSessionWorkflowV2],
            activities=_make_fake_activities(Tracker(), workspace_path=str(workspace)),
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflowV2.run,
                SalesSessionInput(session_id="wa_llm_hb", runtime_workspace_path=str(workspace)),
                id="session-wa_llm_hb",
                task_queue=SALES_QUEUE,
            )
            await handle.signal(HubaraSalesSessionWorkflowV2.send_message, args=["Hola", None, None])
            await handle.result()
            history = await handle.fetch_history()

    scheduled = [
        e.activity_task_scheduled_event_attributes
        for e in history.events
        if e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
        and e.activity_task_scheduled_event_attributes.activity_type.name == "llm_chat"
    ]
    assert scheduled, "el turno no llamó al modelo"
    timeouts = {a.heartbeat_timeout.ToTimedelta() for a in scheduled}
    assert timeouts == {timedelta(seconds=30)}, f"llm_chat agendado sin heartbeat_timeout corto: {timeouts}"
