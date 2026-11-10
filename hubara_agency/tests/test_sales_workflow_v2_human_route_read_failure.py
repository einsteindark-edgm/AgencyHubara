"""Tarjeta del ciclo #15, el caso legítimo que NO debe frenarse: si no se
puede leer quién tiene el chat, el bot responde como antes. Una falla técnica
no le quita el chat al bot ni se lo pasa al equipo."""
from __future__ import annotations

from pathlib import Path

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError

from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2
from tests.test_sales_workflow_debounce import _final_resp
from tests.test_sales_workflow_v2 import _run, _sent

pytestmark = pytest.mark.asyncio


@activity.defn(name="read_reply_window")
async def _unreadable_route(session_id: str) -> dict:
    raise ApplicationError("metadata ilegible", non_retryable=True)


async def test_when_the_route_cannot_be_read_the_bot_still_answers(tmp_path: Path) -> None:
    tracker = await _run(
        HubaraSalesSessionWorkflowV2,
        tmp_path,
        responses=[_final_resp("Claro, te cuento")],
        replace=[_unreadable_route],
    )

    assert _sent(tracker) == ["Claro, te cuento"]
