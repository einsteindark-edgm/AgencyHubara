"""Replay del envío de campañas (R-DET / ADR-005) — barrera contra cambios
de forma de la historia.

- V1 (`CampaignSendWorkflow`): CONGELADO. Las campañas programadas con el tipo
  viejo antes del deploy del V2 arrancan en el worker nuevo y tienen que
  terminar; si alguien toca el V1, esta historia deja de reproducirse.
- V2 (`CampaignSendWorkflowV2` + `CampaignSendBatchWorkflow`): una corrida con
  pausa y reanudación (Updates) y sus dos lotes hijos. Un cambio de forma
  legítimo se versiona (`workflow.patched`) o regenera la fixture con un
  `_v<N>` nuevo: `tests/fixtures/generate_campaign_send_fixtures.py`.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from src.plugins.marketing.agent.campaigns.workflows.send import CampaignSendWorkflow
from src.plugins.marketing.agent.campaigns.workflows.send_v2 import (
    CampaignSendBatchWorkflow,
    CampaignSendWorkflowV2,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _history(name: str, workflow_id: str = "campaign-send-mkt-1") -> WorkflowHistory:
    # El id importa: los lotes hijos se llaman `<id del envío>-batch-<inicio>`.
    return WorkflowHistory.from_json(workflow_id, (FIXTURES / name).read_text(encoding="utf-8"))


def _event_kinds(history: WorkflowHistory) -> set[str]:
    return {event.WhichOneof("attributes") for event in history.events}


@pytest.mark.asyncio
async def test_a_send_scheduled_with_the_old_type_still_replays() -> None:
    history = _history("history_campaign_send_v1.json")
    # Cubre el camino de la baja de Meta (131050), no solo envíos felices.
    assert any(
        e.activity_task_scheduled_event_attributes.activity_type.name == "mark_marketing_opt_out"
        for e in history.events
        if e.HasField("activity_task_scheduled_event_attributes")
    )

    await Replayer(workflows=[CampaignSendWorkflow]).replay_workflow(history)


@pytest.mark.asyncio
async def test_a_batched_send_with_pause_and_resume_replays() -> None:
    history = _history("history_campaign_send_v2.json")
    kinds = _event_kinds(history)
    assert "workflow_execution_update_accepted_event_attributes" in kinds
    assert "start_child_workflow_execution_initiated_event_attributes" in kinds

    await Replayer(workflows=[CampaignSendWorkflowV2, CampaignSendBatchWorkflow]).replay_workflow(
        history
    )


@pytest.mark.parametrize(
    "fixture",
    ["history_campaign_send_batch_v1.json", "history_campaign_send_batch_failed_v1.json"],
)
@pytest.mark.asyncio
async def test_each_batch_replays(fixture: str) -> None:
    await Replayer(workflows=[CampaignSendBatchWorkflow]).replay_workflow(_history(fixture))
