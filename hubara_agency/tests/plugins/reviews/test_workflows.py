"""Los flujos durables de reseñas (Temporal, tiempo acelerado).

* `ReviewRequestWorkflow` (uno por pedido entregado): pregunta qué hacer,
  espera lo que el dominio diga (sin I/O en el workflow — R-DET) y envía una
  sola vez. El reloj lo pone el workflow (`workflow.now()`).
* `ReviewSubmissionWorkflow`: procesa la calificación con reintentos.
"""
from __future__ import annotations

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.reviews.agent.activities import (
    ReviewPlanInput,
    ReviewSendInput,
    ReviewSendOut,
    ReviewStepOut,
    ReviewSubmissionInput,
)
from src.plugins.reviews.agent.requests.workflows import (
    ReviewRequestInput,
    ReviewRequestWorkflow,
    ReviewSubmissionWorkflow,
)

QUEUE = "queue-reviews-test"
HOUR = 3_600_000


def _fakes(steps: list[ReviewStepOut], plans: list, sends: list, submissions: list):
    @activity.defn(name="reviews_plan_step")
    async def fake_plan(inp: ReviewPlanInput) -> ReviewStepOut:
        plans.append(inp)
        return steps[min(len(plans) - 1, len(steps) - 1)]

    @activity.defn(name="reviews_send_request")
    async def fake_send(inp: ReviewSendInput) -> ReviewSendOut:
        sends.append(inp)
        return ReviewSendOut(status="sent", channel="template" if inp.action == "send_template" else "flow")

    @activity.defn(name="reviews_process_submission")
    async def fake_submission(inp: ReviewSubmissionInput) -> str:
        submissions.append(inp)
        return "stored"

    return [fake_plan, fake_send, fake_submission]


async def _run_request(steps: list[ReviewStepOut]):
    plans, sends, submissions = [], [], []
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=QUEUE,
            workflows=[ReviewRequestWorkflow, ReviewSubmissionWorkflow],
            activities=_fakes(steps, plans, sends, submissions),
        ):
            result = await env.client.execute_workflow(
                ReviewRequestWorkflow.run,
                ReviewRequestInput(session_id="wa_1", order_id="order_1", occurred_at_ms=1_791_000_000_000),
                id="review-request-order_1",
                task_queue=QUEUE,
            )
    return result, plans, sends


@pytest.mark.asyncio
async def test_waits_what_the_domain_says_then_sends_once():
    result, plans, sends = await _run_request(
        [
            ReviewStepOut(action="wait", wait_ms=2 * HOUR, reason="min_delay"),
            ReviewStepOut(action="wait", wait_ms=30 * 60_000, reason="waiting_for_window"),
            ReviewStepOut(action="send_template", reason="window_closed"),
        ]
    )
    assert result == "sent"
    assert len(plans) == 3
    assert all(p.delivered_at_ms == 1_791_000_000_000 for p in plans)
    # El reloj avanza con lo que se esperó (tiempo de Temporal, no de pared).
    assert plans[1].now_ms - plans[0].now_ms >= 2 * HOUR
    assert [s.action for s in sends] == ["send_template"]
    assert sends[0].order_id == "order_1"


@pytest.mark.asyncio
async def test_a_skip_ends_without_sending():
    result, plans, sends = await _run_request([ReviewStepOut(action="skip", reason="already_reviewed")])
    assert result == "already_reviewed"
    assert sends == []


@pytest.mark.asyncio
async def test_submission_workflow_processes_the_answer():
    plans, sends, submissions = [], [], []
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=QUEUE,
            workflows=[ReviewRequestWorkflow, ReviewSubmissionWorkflow],
            activities=_fakes([], plans, sends, submissions),
        ):
            result = await env.client.execute_workflow(
                ReviewSubmissionWorkflow.run,
                ReviewSubmissionInput(session_id="wa_1", wa_message_id="wamid.1", response_json="{}", received_at_ms=1),
                id="review-submit-wamid.1",
                task_queue=QUEUE,
            )
    assert result == "stored"
    assert submissions[0].wa_message_id == "wamid.1"
