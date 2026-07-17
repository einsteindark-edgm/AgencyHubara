"""CampaignSendWorkflow — orquesta plan → sends → touches → resultado.

WorkflowEnvironment time-skipping + activities fake con tracker (R-DET).
Un destinatario que falla NO tumba la campaña: se contabiliza y sigue.
"""
from __future__ import annotations

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.marketing.agent.campaigns.workflows.send import (
    CampaignSendWorkflow,
)
from src.plugins.marketing.domain.campaigns import (
    CampaignSendPlan,
    RecipientSendPlan,
    SkippedRecipient,
)
from src.sdk import get_task_queue

QUEUE = get_task_queue("marketing", "campaigns")

_PLAN = CampaignSendPlan(
    campaign_id="mkt-1",
    template_name="campaign_promo_marketing_v1",
    recipients=[
        RecipientSendPlan(session_id="wa_a", variables={"greeting": "Hola A"}),
        RecipientSendPlan(session_id="wa_b", variables={"greeting": "Hola B"}),
        RecipientSendPlan(session_id="wa_c", variables={"greeting": "Hola C"}),
    ],
    skipped=[SkippedRecipient(session_id="wa_z", reason="excluido")],
    unit_cost_usd_micros=12500,
    total_cost_usd_micros=37500,
)


class Tracker:
    def __init__(self) -> None:
        self.sends: list[tuple[str, str, dict]] = []
        self.touches: list[tuple[str, str, str]] = []
        self.statuses: list[str] = []
        self.result: dict | None = None


def _fakes(tracker: Tracker, *, fail_session: str | None = None):
    @activity.defn(name="load_campaign_send_plan")
    async def fake_plan(campaign_id: str) -> CampaignSendPlan:
        return _PLAN

    @activity.defn(name="mark_campaign_sending")
    async def fake_mark(campaign_id: str) -> None:
        tracker.statuses.append("sending")

    @activity.defn(name="send_whatsapp_template_activity")
    async def fake_send(
        session_id: str, template_name: str, variables: dict
    ) -> dict:
        if session_id == fail_session:
            raise ApplicationError(
                "Meta 131026 non-retryable", non_retryable=True
            )
        tracker.sends.append((session_id, template_name, variables))
        return {"wa_message_id": f"wamid-{session_id}", "ok": True, "error": None}

    @activity.defn(name="stamp_campaign_touch")
    async def fake_touch(
        session_id: str, campaign_id: str, campaign_name: str
    ) -> None:
        tracker.touches.append((session_id, campaign_id, campaign_name))

    @activity.defn(name="record_campaign_send_result")
    async def fake_record(campaign_id: str, result: dict) -> None:
        tracker.result = result

    return [fake_plan, fake_mark, fake_send, fake_touch, fake_record]


async def _run(tracker: Tracker, **kw) -> dict:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=QUEUE,
            workflows=[CampaignSendWorkflow],
            activities=_fakes(tracker, **kw),
        ):
            return await env.client.execute_workflow(
                CampaignSendWorkflow.run,
                args=["mkt-1", "Promo madre"],
                id="campaign-send-test",
                task_queue=QUEUE,
            )


@pytest.mark.asyncio
async def test_envia_a_cada_destinatario_y_estampa_touch() -> None:
    tracker = Tracker()
    summary = await _run(tracker)

    assert [s[0] for s in tracker.sends] == ["wa_a", "wa_b", "wa_c"]
    assert all(s[1] == "campaign_promo_marketing_v1" for s in tracker.sends)
    assert [t[0] for t in tracker.touches] == ["wa_a", "wa_b", "wa_c"]
    assert tracker.touches[0][1:] == ("mkt-1", "Promo madre")
    assert tracker.result is not None
    assert tracker.result["sent"] == 3
    assert tracker.result["failed"] == []
    assert tracker.result["spent_usd_micros"] == 37500
    assert summary["sent"] == 3


@pytest.mark.asyncio
async def test_destinatario_que_falla_no_tumba_la_campana() -> None:
    tracker = Tracker()
    summary = await _run(tracker, fail_session="wa_b")

    assert [s[0] for s in tracker.sends] == ["wa_a", "wa_c"]
    # Sin send exitoso no hay touch (no atribuimos lo que no llegó).
    assert [t[0] for t in tracker.touches] == ["wa_a", "wa_c"]
    assert tracker.result["sent"] == 2
    assert tracker.result["failed"] == ["wa_b"]
    assert tracker.result["spent_usd_micros"] == 25000
    assert summary["failed"] == 1
