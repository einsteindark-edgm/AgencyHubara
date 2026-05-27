"""Completion events emitted by ``chats`` workflows.

These dataclasses are the **boundary type** between sibling sub-agents:
``sales`` emits ``SalesSessionCompletionEvent`` → dispatcher reads manifest
``transitions[]`` → routes to ``remarketing`` (or terminates). Same for
``CustomerRepliedDuringRemarketingEvent`` going back to Sales.

Neither sales/ nor remarketing/ imports the other — both import these events
from ``chats.shared.contracts.events``. That's how R-DIP #10 holds without
exceptions in the importlinter.

Conventions (enforced by ``tests/architecture/test_events_consistency.py``):

- ``@dataclass(frozen=True)`` — R-JSON, hashable, replay-safe
- Class name ends with ``Event``
- All fields are JSON-serializable (str / int / float / bool / None / nested
  frozen dataclasses, list/dict of those)
- ``session_id: str`` is conventionally the first field (so the default
  workflow_id_template ``{event.session_id}`` works without explicit override)

See ADR-2026-05-20 §3 and ``src.platform.orchestration.events`` for the
dispatcher contract.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SalesSessionCompletionEvent:
    """Sales-side completion / transition event.

    Emitted by ``HubaraSalesSessionWorkflow`` when:
      - the agent classifies the conversation with a terminal tag
        (e.g. ``INTERESADO``, ``HUMANO``, ``GHOSTED``)
      - and decides whether to schedule a follow-up (remarketing) or close.

    The ``tag`` field drives the manifest transition. The current manifest
    declares only one transition (``tag == INTERESADO`` → start
    ``RemarketingWorkflow`` with motivo + delay). All other tags are
    terminal-as-far-as-the-dispatcher is concerned: the event is emitted for
    observability (system_map shows it), but no transition fires.

    Fields:
        session_id: the chats session id (used as ``{event.session_id}``
            token in the workflow_id_template of the target workflow).
        tag: classification result. Free-form string; matched literally by
            the manifest ``when:`` clauses. Use the constants from
            ``src.platform.constants`` to avoid typos.
        motivo: short human-readable reason. Travels to the target workflow
            via ``input_mapping`` (e.g. seeded into RemarketingSessionInput).
            Empty string for terminal cases that don't transition.
        delay_seconds: how long to wait before the target workflow starts.
            Used as ``start_delay`` for ``via=start_workflow*`` actions.
            Zero means immediate dispatch.
    """

    session_id: str
    tag: str
    motivo: str = ""
    delay_seconds: int = 60


@dataclass(frozen=True)
class CustomerRepliedDuringRemarketingEvent:
    """Remarketing-side handoff event.

    Emitted by ``RemarketingWorkflow`` when the customer replies *during*
    the remarketing cycle — at which point we hand control back to Sales so
    the human-like sales agent picks up the conversation.

    The dispatcher consumes this event via a manifest transition that:
      1. (planned future step) writes ``pending_handoff_summary`` to the
         session metadata so Sales' bootstrap picks it up — currently handled
         by ``write_pending_handoff_activity`` invoked by the workflow
         BEFORE emitting the event. See ADR-2026-05-20 §6.
      2. ``ensure_running`` on the Sales workflow with id ``session-{session_id}``.

    Fields:
        session_id: the chats session id.
        summary: short summary of the remarketing context to forward to Sales.
            Currently NOT propagated via the dispatcher (it's pre-written to
            metadata by ``write_pending_handoff_activity``). The field lives
            in the event for observability / future migration.
    """

    session_id: str
    summary: str = ""


# =============================================================================
# HU-WA24H-001 Sprint 2 — Service window watchdog events
# =============================================================================
#
# Three lifecycle events tied to the WhatsApp 24h service window. The
# watchdog workflow (`ServiceWindowWatchdogWorkflow`) is started, signaled
# (reschedule / cancel) and cancelled in response to these events via the
# manifest declarative orchestration (ADR-2026-05-20). No sibling workflow
# imports — sales/remarketing only depend on this module + the dispatcher.


@dataclass(frozen=True)
class ServiceWindowOpenedEvent:
    """Emitted by IngestInboundMessage when an inbound (re)opens the 24h
    WhatsApp service window for an active episode.

    The dispatcher consumes this via a `start_workflow_with_replace`
    transition that starts the watchdog with `start_delay` = `fire_at_ms -
    now`. If an existing watchdog is running for the same
    `(session_id, episode_id)`, the replace verb terminates it and starts a
    fresh one — that's how consecutive inbounds extend the window without
    growing parallel watchdogs.

    Fields:
        session_id: the chats session id (`wa_<phone>`).
        episode_id: the active episode id (`ep_NNN`).
        fire_at_ms: epoch ms when the watchdog should attempt the nudge.
            Equal to `service_window_expires_at_ms - WATCHDOG_PRE_EXPIRY_MS`
            (30 min before the window closes). The dispatcher does NOT use
            this for `start_delay` directly — the workflow body sleeps to
            `fire_at_ms` itself so reschedules can happen via signal.
        suggested_template_kind: hint about the template type the watchdog
            should resolve at fire time (`"quote_pending"` /
            `"payment_pending"` / `"order_status"`). The actual template
            resolution happens fresh in the eligibility activity 23.5h
            later, since the episode stage may have evolved.
    """

    session_id: str
    episode_id: str
    fire_at_ms: int
    suggested_template_kind: str = ""


@dataclass(frozen=True)
class CustomerRepliedEvent:
    """Emitted when a customer inbound arrives while a watchdog workflow
    is already scheduled.

    Routed by manifest via `via: signal, signal_handler: cancel_watchdog`
    to the watchdog workflow id `watchdog-{session_id}-{episode_id}`. The
    signal cancels the watchdog cleanly — the customer already responded,
    no nudge needed.

    Fields:
        session_id: the chats session id.
        episode_id: the active episode id. The watchdog is per-episode, so
            the cancellation targets the right workflow id even if multiple
            historical watchdogs exist for the session.
    """

    session_id: str
    episode_id: str


@dataclass(frozen=True)
class EpisodeClosedEvent:
    """Emitted when an episode formally closes (COMPRA_EXITOSA / RECHAZO /
    CONFIRMADO_SIN_DATOS / CONFIRMADO_PAGO_PENDIENTE / TIMEOUT).

    Routed by manifest via `via: signal, signal_handler: cancel_watchdog`
    — same target workflow id template as `CustomerRepliedEvent`. The
    watchdog has no point firing if the deal is closed; the signal stops
    it before it sends a nudge.

    Fields:
        session_id: the chats session id.
        episode_id: the episode that closed.
        closing_tag: the closing tag string. Carried for observability +
            potential routing logic in future transitions (e.g., a
            `RECHAZO` could also block cadence whereas `COMPRA_EXITOSA`
            might trigger a post-sale onboarding workflow).
    """

    session_id: str
    episode_id: str
    closing_tag: str


__all__ = [
    "SalesSessionCompletionEvent",
    "CustomerRepliedDuringRemarketingEvent",
    "ServiceWindowOpenedEvent",
    "CustomerRepliedEvent",
    "EpisodeClosedEvent",
]
