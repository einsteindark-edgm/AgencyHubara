"""Declarative cross-worker orchestration (ADR-2026-05-20).

Workflows emit ``CompletionEvent`` instances; the manifest declares
``transitions[]`` that map ``on_event + when`` to a target ``action``;
``dispatch_event_activity`` reads the manifest, resolves matching transitions
and starts/signals the target workflows via Temporal — **without the source
workflow ever importing the target workflow's class**.

This module is the runtime backbone for Level 3 declarative orchestration
documented in ``ADR-2026-05-20-declarative-orchestration.md``. The previous
Level 2 design (``ADR-2026-05-19-string-based-workflow-dispatch.md``) is
superseded but its helper ``get_workflow_name`` lives on as the lookup
primitive used by the dispatcher.

Public surface:
    - ``EventEnvelope`` — JSON-serializable wrapper around an emitted event
    - ``envelope_for(event, plugin, worker)`` — build the envelope from a
      ``@dataclass`` event instance
    - ``Transition`` / ``TransitionAction`` — typed representation of a
      manifest ``transitions[]`` entry
    - ``dispatch_event_activity`` — Temporal activity that the workflow calls
      via ``workflow.execute_activity(dispatch_event_activity, envelope)``

Workflows should depend ONLY on this module + their own ``shared/contracts``
events. They MUST NOT import sibling workflow classes (R-DIP #10).
"""
from __future__ import annotations

from src.platform.orchestration.dispatcher import (
    DispatchResult,
    dispatch_envelope_with_client,
    dispatch_event_activity,
)
from src.platform.orchestration.events import (
    EventEnvelope,
    envelope_for,
    event_get,
    event_to_dict,
    event_type_name,
)
from src.platform.orchestration.transitions import Transition, TransitionAction

__all__ = [
    "DispatchResult",
    "EventEnvelope",
    "Transition",
    "TransitionAction",
    "dispatch_envelope_with_client",
    "dispatch_event_activity",
    "envelope_for",
    "event_get",
    "event_to_dict",
    "event_type_name",
]
