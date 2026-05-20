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
