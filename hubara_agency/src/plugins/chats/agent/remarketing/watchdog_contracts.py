"""Boundary DTOs for the ServiceWindowWatchdogWorkflow (HU-WA24H-001 Sprint 2).

Kept in a separate module from `contracts.py` (which holds
`RemarketingSessionInput`) so the watchdog can ship independently and so the
fixture pool of "things that cross workflow boundaries" is grep-able by
filename — `watchdog_contracts.py` = the watchdog's R-JSON surface.

All dataclasses are `@dataclass(frozen=True)` and contain only primitive
JSON-serializable types (R-JSON). They cross `workflow.execute_activity` /
`workflow.run` boundaries.

NO `from __future__ import annotations` — see the dispatcher docstring
(`src/platform/orchestration/dispatcher.py`) for the rationale: Temporal's
DataConverter calls `get_type_hints` from inside the workflow sandbox, and
PEP 563 string annotations fail to resolve nested types there.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class WatchdogInput:
    """Input for `ServiceWindowWatchdogWorkflow.run`.

    Built by the dispatcher from `ServiceWindowOpenedEvent` via the
    manifest `input_mapping`. The workflow id derived from this input is
    `watchdog-{session_id}-{episode_id}` (set by `workflow_id_template` in
    the manifest).

    Fields:
        session_id: the chats session id (`wa_<phone>`).
        episode_id: the active episode id.
        fire_at_ms: epoch ms when the watchdog should attempt the nudge.
            Stored as int (not datetime) per R-JSON. The workflow uses
            `workflow.now()` to derive the sleep delta deterministically.
        suggested_template_kind: hint from `IngestInboundMessage` about
            what template kind would be appropriate at fire time. Used as
            a tiebreaker when the eligibility activity resolves the
            template — fresh data from `metadata` always wins, but if
            two templates match equally, this nudges the choice.
    """

    session_id: str
    episode_id: str
    fire_at_ms: int
    suggested_template_kind: str = ""


@dataclass(frozen=True)
class WatchdogEligibilityResult:
    """Result of `check_watchdog_eligibility_activity`.

    Encapsulates the "should we fire?" decision plus the resolved template
    when the decision is yes. Frozen — the workflow logs/uses it but never
    mutates it.

    Fields:
        eligible: True if the watchdog should send the template now,
            False if it should skip (with `reason` populated).
        reason: short string describing why the watchdog was NOT fired.
            Examples: `"feature_flag_off"`, `"active_route_humano"`,
            `"episode_closed"`, `"window_not_expiring_soon"`,
            `"no_template_for_stage"`.  None when `eligible=True`.
        resolved_template_name: the registry key of the template to send
            (e.g. `"quote_ready_utility_v2"`). None when `eligible=False`.
        resolved_template_variables: dict of variable name → value to
            interpolate into the template. None when `eligible=False`.
            Mapped to `dict[str, str]` to keep R-JSON simple — if a
            template ever needs structured variables, escalate to a
            nested frozen dataclass.
    """

    eligible: bool
    reason: Optional[str] = None
    resolved_template_name: Optional[str] = None
    resolved_template_variables: Optional[dict] = None


@dataclass(frozen=True)
class WatchdogState:
    """Snapshot of the watchdog block of `metadata.json`.

    Persisted by `persist_watchdog_outcome_activity`. Mirrors the JSON
    shape declared in HU-WA24H-001 §3.1:

        "watchdog": {
          "workflow_id": ...,
          "scheduled_for_ms": ...,
          "fired_at_ms": ...,
          "cancelled_at_ms": ...,
          "reason_cancelled": ...
        }

    Both reads (eligibility) and writes (outcome) round-trip through this
    type so the JSON contract is centralized.

    Fields:
        workflow_id: the running watchdog workflow id, or None.
        scheduled_for_ms: epoch ms when the watchdog was scheduled to fire.
        fired_at_ms: epoch ms when it actually fired, or None.
        cancelled_at_ms: epoch ms when it was cancelled, or None.
        reason_cancelled: human-readable cancellation reason
            (`"customer_replied"`, `"episode_closed"`, `"deal_closed"`).
            None when not cancelled.
    """

    workflow_id: Optional[str] = None
    scheduled_for_ms: Optional[int] = None
    fired_at_ms: Optional[int] = None
    cancelled_at_ms: Optional[int] = None
    reason_cancelled: Optional[str] = None
