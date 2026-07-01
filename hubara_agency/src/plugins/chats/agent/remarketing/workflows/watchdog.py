"""ServiceWindowWatchdogWorkflow — HU-WA24H-001 Sprint 2.

One workflow per active episode. Sleeps until 30 min before the WhatsApp
24h service window closes, then fires a utility template if (and only if)
the customer hasn't replied and the episode is still open.

Cancelled via signal from two sources, both routed by manifest:
  * `CustomerRepliedEvent` (IngestInboundMessage)
  * `EpisodeClosedEvent` (ManageConversationTagTool callers)

Workflow id template: `watchdog-{session_id}-{episode_id}`. Per-episode so
that closing an episode cleanly cancels exactly this watchdog without
interfering with the next episode's watchdog (which gets its own workflow
id).

R-DET notes:
  * `workflow.now()` and `workflow.wait_condition` ONLY — no `datetime.now()`,
    no `time.sleep`, no `random`. The sleep window is computed from
    `WatchdogInput.fire_at_ms` (input data) and `workflow.now()`.
  * `workflow.patched("watchdog-v1")` gates the entire body so future
    behaviour changes can roll out without breaking in-flight watchdogs
    that started before the deploy.

Determinism gotcha: NO `from __future__ import annotations` — same reasoning
as `src/platform/orchestration/dispatcher.py`. The workflow's input dataclass
(`WatchdogInput`) and the eligibility return dataclass
(`WatchdogEligibilityResult`) are reconstructed by Temporal's DataConverter
inside the workflow sandbox via `get_type_hints`. PEP 563 string annotations
fail to eval inside that sandbox.
"""
import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from src.plugins.chats.agent.remarketing.activities.watchdog_activities import (
        check_watchdog_eligibility_activity,
        persist_watchdog_outcome_activity,
    )
    from src.platform.whatsapp.activities import (
        send_whatsapp_template_activity,
    )
    from src.plugins.chats.agent.remarketing.watchdog_contracts import (
        WatchdogInput,
    )


@workflow.defn(name="ServiceWindowWatchdogWorkflow")
class ServiceWindowWatchdogWorkflow:
    """Per-episode 24h-window watchdog.

    Lifecycle:
      1. start  → schedule the wait until `fire_at_ms`.
      2. wait_condition (cancel_signal OR timeout):
         * cancelled  → persist `cancelled` + reason, return.
         * timeout    → continue to eligibility check.
      3. eligibility check (defense-in-depth re-validation):
         * not eligible → persist `skipped` + reason, return.
         * eligible     → continue to send.
      4. send template — REAL vía send_whatsapp_template_activity (Sprint 3).
         * fail   → persist `failed` + error, re-raise so Temporal records it.
         * ok     → persist `fired` + wa_message_id, return.

    The activities are all <10s — `start_to_close_timeout=15s` is generous
    enough to absorb a slow filesystem read without inviting retry storms.
    Retry policies are conservative (`maximum_attempts=2..3`) because every
    retry of the send risks a duplicate template charge if Meta acked but
    we lost the reply.
    """

    def __init__(self) -> None:
        # Cancellation latch. Multiple signals can arrive (customer replied
        # AND episode closed within the same tick) — first one wins; the
        # rest are no-ops because the wait_condition only checks truthiness.
        self._cancelled: bool = False
        self._cancel_reason: str | None = None

    @workflow.signal
    async def cancel_watchdog(self, payload: dict) -> None:
        """Cancel the watchdog. Sources (via manifest):

        * `CustomerRepliedEvent`  → `{"reason": "customer_replied"}`
        * `EpisodeClosedEvent`    → `{"reason": "<closing_tag>"}`

        The signal accepts a dict because the generic dispatcher
        (`dispatch_event_activity`) always passes a dict built from
        `input_mapping`. To keep direct programmatic signals ergonomic
        (e.g. tests, ad-hoc ops scripts), `payload` may also be a plain
        string — we coerce both shapes to a `_cancel_reason: str`.

        First signal sets `_cancelled=True`; subsequent signals only refresh
        the reason. In practice both transitions (reply + close) arrive
        close together and either reason is valid for observability.
        """
        if isinstance(payload, dict):
            reason = payload.get("reason") or "unspecified"
        elif payload is None:
            reason = "unspecified"
        else:
            # Defensive: also accept a raw string for direct callers.
            reason = str(payload)
        self._cancelled = True
        self._cancel_reason = reason

    @workflow.run
    async def run(self, input: WatchdogInput) -> None:
        # All execution gated by patched("watchdog-v1") so we have a single
        # rollback point if the watchdog needs to be disabled at the workflow
        # level (vs the activity-level WATCHDOG_ENABLED env var, which only
        # blocks the SEND but still runs the wait + eligibility for
        # observability).
        if not workflow.patched("watchdog-v1"):
            # Pre-deploy histories don't carry the watchdog logic — but
            # ServiceWindowWatchdogWorkflow only exists post-deploy, so any
            # replay reaching this branch is a defensive impossibility. Log
            # and exit cleanly to avoid blowing up replay tests.
            workflow.logger.info(
                "ServiceWindowWatchdogWorkflow disabled (patch v1 not set)",
                extra={"session_id": input.session_id},
            )
            return

        # 1. Sleep until fire_at_ms, cancellable.
        # Use workflow.now() (datetime, deterministic) to compute the delta.
        # Convert to ms since epoch via .timestamp() * 1000.
        now_ms = int(workflow.now().timestamp() * 1000)
        delta_ms = input.fire_at_ms - now_ms

        if delta_ms > 0:
            try:
                await workflow.wait_condition(
                    lambda: self._cancelled,
                    timeout=timedelta(milliseconds=delta_ms),
                )
            except asyncio.TimeoutError:
                # Timer ran out — window is now ≤ 30min from closing.
                # Proceed to eligibility re-check.
                pass

        # 2a. Cancelled before / during the wait → persist + exit.
        if self._cancelled:
            await workflow.execute_activity(
                persist_watchdog_outcome_activity,
                args=[
                    input.session_id,
                    "cancelled",
                    self._cancel_reason or "unspecified",
                ],
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
            workflow.logger.info(
                f"Watchdog cancelled for session={input.session_id} "
                f"episode={input.episode_id} reason={self._cancel_reason}"
            )
            return

        # 2b. Re-check eligibility (defense-in-depth — state moved while we
        # slept hours).
        eligibility = await workflow.execute_activity(
            check_watchdog_eligibility_activity,
            args=[input.session_id, input.episode_id],
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )

        if not eligibility.eligible:
            await workflow.execute_activity(
                persist_watchdog_outcome_activity,
                args=[
                    input.session_id,
                    "skipped",
                    eligibility.reason or "unspecified",
                ],
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
            workflow.logger.info(
                f"Watchdog skipped for session={input.session_id} "
                f"episode={input.episode_id} reason={eligibility.reason}"
            )
            return

        # 3. Fire the template — REAL send (Sprint 3). Delega en la activity
        # productiva `send_whatsapp_template_activity` (platform/whatsapp):
        # resuelve el TemplateSpec, POSTea a la Cloud API, persiste el
        # OutboundLogEntry con pricing pendiente e idempotencia. Gated aguas
        # arriba por `WATCHDOG_ENABLED` (check_watchdog_eligibility_activity).
        try:
            result = await workflow.execute_activity(
                send_whatsapp_template_activity,
                args=[
                    input.session_id,
                    eligibility.resolved_template_name,
                    eligibility.resolved_template_variables or {},
                ],
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
        except Exception as exc:
            # Persist `failed` BEFORE re-raising so the dashboard reflects
            # the outcome even when Temporal subsequently records the
            # workflow as failed. We re-raise so the workflow's history
            # captures the error — silent swallow would hide a real
            # production issue.
            await workflow.execute_activity(
                persist_watchdog_outcome_activity,
                args=[input.session_id, "failed", str(exc)[:200]],
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
            raise

        # 4. Persist `fired` outcome with the real Meta wa_message_id.
        await workflow.execute_activity(
            persist_watchdog_outcome_activity,
            args=[input.session_id, "fired", result.wa_message_id or ""],
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        workflow.logger.info(
            f"Watchdog fired for session={input.session_id} "
            f"episode={input.episode_id} template={eligibility.resolved_template_name}"
        )
