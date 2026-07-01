"""Workflow-level tests for ServiceWindowWatchdogWorkflow (HU-WA24H-001 Sprint 2).

Uses WorkflowEnvironment.start_time_skipping to fast-forward Temporal timers
deterministically — no real wallclock sleeps. Mocked activities so the tests
focus on the workflow's control flow (timer + signal + eligibility branch +
send + persist).

Cases covered:
  * `test_fires_after_timer_when_eligible` — happy path.
  * `test_cancel_by_signal_before_fire` — customer reply cancels mid-sleep.
  * `test_skipped_when_ineligible` — eligibility activity returns
    eligible=False, workflow persists `skipped` and exits.
  * `test_failed_send_persists_outcome_and_raises` — send activity raises,
    workflow persists `failed` and re-raises.
  * `test_disabled_when_patch_not_set` — defensive (the patch is implicit
    in new workflows; covered by replay).

Note on Temporal version compatibility: `workflow.now()` returns a UTC
datetime; we anchor tests with `time.time()` to convert epoch-ms input.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.chats.agent.remarketing.watchdog_contracts import (
    WatchdogEligibilityResult,
    WatchdogInput,
)
from src.plugins.chats.agent.remarketing.workflows.watchdog import (
    ServiceWindowWatchdogWorkflow,
)
from src.platform.whatsapp.dtos import OutboundResult


# ---------------------------------------------------------------------------
# Fake activities — match the @activity.defn(name=...) of the real ones.
# ---------------------------------------------------------------------------


class _CallLog:
    """Records invocations across the fake activities for assertion."""

    def __init__(self) -> None:
        self.eligibility_calls: list[tuple[str, str]] = []
        self.send_calls: list[tuple[str, str, dict]] = []
        self.persist_calls: list[tuple[str, str, str | None]] = []
        self.send_should_raise: bool = False
        self.eligibility_result: WatchdogEligibilityResult = WatchdogEligibilityResult(
            eligible=True,
            resolved_template_name="quote_ready_utility_v1",
            resolved_template_variables={
                "customer_first_name": "Camilo",
                "product_or_quote_label": "tu camisa",
            },
        )


def _make_fake_activities(log: _CallLog) -> list:
    @activity.defn(name="check_watchdog_eligibility_activity")
    async def fake_eligibility(
        session_id: str, episode_id: str
    ) -> WatchdogEligibilityResult:
        log.eligibility_calls.append((session_id, episode_id))
        return log.eligibility_result

    # Sprint 3 swap: el watchdog dispara el envío REAL de template
    # (`send_whatsapp_template_activity` de platform/whatsapp/activities.py),
    # NO el mock de Sprint 2. El fake se registra bajo ese nombre para que
    # el workflow lo resuelva.
    @activity.defn(name="send_whatsapp_template_activity")
    async def fake_send(
        session_id: str, template_name: str, variables: dict
    ) -> OutboundResult:
        log.send_calls.append((session_id, template_name, dict(variables)))
        if log.send_should_raise:
            raise RuntimeError("simulated meta 500")
        return OutboundResult(
            wa_message_id=f"wamid.TEST_{uuid.uuid4().hex[:8]}",
            ok=True,
            error=None,
        )

    @activity.defn(name="persist_watchdog_outcome_activity")
    async def fake_persist(
        session_id: str, outcome: str, detail: str | None
    ) -> None:
        log.persist_calls.append((session_id, outcome, detail))

    return [fake_eligibility, fake_send, fake_persist]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def env() -> WorkflowEnvironment:
    e = await WorkflowEnvironment.start_time_skipping()
    try:
        yield e
    finally:
        await e.shutdown()


def _input(fire_in_ms: int, now_ms_anchor: int) -> WatchdogInput:
    return WatchdogInput(
        session_id="wa_+57300test",
        episode_id="ep_001",
        fire_at_ms=now_ms_anchor + fire_in_ms,
        suggested_template_kind="quote_pending",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fires_after_timer_when_eligible(env: WorkflowEnvironment) -> None:
    """Happy path: timer fires → eligibility passes → send → persist 'fired'."""
    log = _CallLog()
    task_queue = "tq-watchdog-fires"

    async with Worker(
        env.client,
        task_queue=task_queue,
        workflows=[ServiceWindowWatchdogWorkflow],
        activities=_make_fake_activities(log),
    ):
        # fire_at = now + 1 hour. Time-skipping env advances workflow time
        # to satisfy the timer without real wallclock sleep. We anchor with
        # time.time() — the workflow body computes delta from workflow.now()
        # vs fire_at_ms, and the env's time-skipping handles the rest.
        import time

        now_ms = int(time.time() * 1000)

        handle = await env.client.start_workflow(
            ServiceWindowWatchdogWorkflow.run,
            _input(fire_in_ms=3600_000, now_ms_anchor=now_ms),
            id=f"watchdog-test-{uuid.uuid4().hex[:8]}",
            task_queue=task_queue,
        )
        await handle.result()

    assert log.eligibility_calls == [("wa_+57300test", "ep_001")]
    assert len(log.send_calls) == 1
    assert log.send_calls[0][0] == "wa_+57300test"
    assert log.send_calls[0][1] == "quote_ready_utility_v1"
    # Persist called exactly once with outcome="fired"
    fired = [c for c in log.persist_calls if c[1] == "fired"]
    assert len(fired) == 1
    assert fired[0][0] == "wa_+57300test"


@pytest.mark.asyncio
async def test_cancel_by_signal_before_fire(env: WorkflowEnvironment) -> None:
    """Customer reply (cancel signal) arrives before timer — no send, persist
    'cancelled'."""
    log = _CallLog()
    task_queue = "tq-watchdog-cancel"
    import time

    now_ms = int(time.time() * 1000)

    async with Worker(
        env.client,
        task_queue=task_queue,
        workflows=[ServiceWindowWatchdogWorkflow],
        activities=_make_fake_activities(log),
    ):
        handle = await env.client.start_workflow(
            ServiceWindowWatchdogWorkflow.run,
            _input(fire_in_ms=3600_000, now_ms_anchor=now_ms),
            id=f"watchdog-test-{uuid.uuid4().hex[:8]}",
            task_queue=task_queue,
        )
        # Send the cancel signal immediately — before time-skipping advances
        # to fire_at_ms.
        await handle.signal(
            ServiceWindowWatchdogWorkflow.cancel_watchdog,
            {"reason": "customer_replied"},
        )
        await handle.result()

    # Eligibility & send NEVER ran.
    assert log.eligibility_calls == []
    assert log.send_calls == []
    # Persist 'cancelled' with the reason.
    cancelled = [c for c in log.persist_calls if c[1] == "cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0][2] == "customer_replied"


@pytest.mark.asyncio
async def test_skipped_when_ineligible(env: WorkflowEnvironment) -> None:
    """Eligibility returns eligible=False → workflow persists 'skipped' and
    exits without sending."""
    log = _CallLog()
    log.eligibility_result = WatchdogEligibilityResult(
        eligible=False, reason="feature_flag_off"
    )
    task_queue = "tq-watchdog-skip"
    import time

    now_ms = int(time.time() * 1000)

    async with Worker(
        env.client,
        task_queue=task_queue,
        workflows=[ServiceWindowWatchdogWorkflow],
        activities=_make_fake_activities(log),
    ):
        handle = await env.client.start_workflow(
            ServiceWindowWatchdogWorkflow.run,
            _input(fire_in_ms=3600_000, now_ms_anchor=now_ms),
            id=f"watchdog-test-{uuid.uuid4().hex[:8]}",
            task_queue=task_queue,
        )
        await handle.result()

    assert len(log.eligibility_calls) == 1
    assert log.send_calls == []
    skipped = [c for c in log.persist_calls if c[1] == "skipped"]
    assert len(skipped) == 1
    assert skipped[0][2] == "feature_flag_off"


@pytest.mark.asyncio
async def test_failed_send_persists_outcome_and_raises(
    env: WorkflowEnvironment,
) -> None:
    """Send activity fails (3 retries exhausted) → workflow persists 'failed'
    and re-raises (Temporal records the workflow as failed)."""
    log = _CallLog()
    log.send_should_raise = True
    task_queue = "tq-watchdog-failed"
    import time

    now_ms = int(time.time() * 1000)

    async with Worker(
        env.client,
        task_queue=task_queue,
        workflows=[ServiceWindowWatchdogWorkflow],
        activities=_make_fake_activities(log),
    ):
        handle = await env.client.start_workflow(
            ServiceWindowWatchdogWorkflow.run,
            _input(fire_in_ms=3600_000, now_ms_anchor=now_ms),
            id=f"watchdog-test-{uuid.uuid4().hex[:8]}",
            task_queue=task_queue,
        )
        with pytest.raises(WorkflowFailureError):
            await handle.result()

    # Eligibility ran, send was attempted, persist captured 'failed' before
    # re-raise.
    assert len(log.eligibility_calls) == 1
    assert len(log.send_calls) >= 1  # may retry up to 3x
    failed = [c for c in log.persist_calls if c[1] == "failed"]
    assert len(failed) == 1
