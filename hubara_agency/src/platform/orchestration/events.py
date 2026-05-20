"""Completion events emitted by workflows + helpers.

A *completion event* is any ``@dataclass(frozen=True)`` instance that a
workflow emits to signal a transition (end of session, threshold crossed,
external trigger, etc.). The dispatcher matches these against manifest
``transitions[]`` to decide what runs next.

**Conventions (enforced by tests):**

- Events live in ``src.plugins.<plugin>.shared.contracts.events`` (or
  ``src.platform.contracts`` for events that cross plugin boundaries).
- Class name MUST end with ``Event`` (e.g. ``SalesSessionCompletionEvent``).
- Class MUST be ``@dataclass(frozen=True)`` and contain only JSON-serializable
  fields (str / int / float / bool / None / list / dict / nested dataclass) —
  R-JSON.
- The first argument is conventionally ``session_id: str`` so that the
  default ``workflow_id_template`` resolves without an explicit ``input_mapping``.

The wrapper ``EventEnvelope`` carries the event across the
workflow → activity boundary as a plain dict (avoids depending on Temporal's
DataConverter knowing every event subclass).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Mapping


def event_type_name(event: Any) -> str:
    """Canonical name of an event = the Python class name.

    Used both by the dispatcher (matching ``on_event``) and by the
    architecture tests (drift detection against ``emits[]``).
    """
    return type(event).__name__


def event_to_dict(event: Any) -> dict[str, Any]:
    """Serialize a dataclass event to a plain dict.

    Raises ``TypeError`` if ``event`` is not a dataclass — every emitted
    event must follow R-JSON.
    """
    if not is_dataclass(event):
        raise TypeError(
            f"Event {event!r} is not a @dataclass — completion events MUST "
            f"be frozen dataclasses (R-JSON). See "
            f"src.platform.orchestration.events docstring."
        )
    return asdict(event)


def event_get(event: Any, field_name: str) -> Any:
    """Read a field from an event. Used by ``Transition.matches``.

    Raises ``AttributeError`` if the field does not exist — caller decides
    whether that's a "no match" (transitions) or a programming error (tests).
    """
    return getattr(event, field_name)


@dataclass(frozen=True)
class EventEnvelope:
    """JSON-serializable wrapper carried from workflow → dispatcher activity.

    Why a wrapper instead of passing the event directly?
        Temporal's DataConverter needs concrete type hints to deserialize
        dataclasses. ``dispatch_event_activity`` is generic — it doesn't
        know every event subclass — so it accepts the envelope (plain dict
        payload + string type name) and matches by type name against the
        manifest. The cost is a single ``asdict`` round-trip; the win is
        zero coupling between dispatcher and event types.

    Fields:
        event_type: canonical class name (``type(event).__name__``)
        payload: ``asdict(event)`` — flat JSON-serializable dict
        source_plugin: id of the plugin that owns the source worker
        source_worker: ``agent.workers[].name`` of the source worker
    """

    event_type: str
    payload: Mapping[str, Any]
    source_plugin: str
    source_worker: str


def envelope_for(event: Any, *, source_plugin: str, source_worker: str) -> EventEnvelope:
    """Build the envelope for an emitted event.

    Example:
        >>> from src.plugins.chats.shared.contracts.events import (
        ...     SalesSessionCompletionEvent,
        ... )
        >>> ev = SalesSessionCompletionEvent(
        ...     session_id="abc",
        ...     tag="INTERESADO",
        ...     summary="...",
        ...     delay_seconds=60,
        ... )
        >>> env = envelope_for(ev, source_plugin="chats", source_worker="sales")
        >>> env.event_type
        'SalesSessionCompletionEvent'
        >>> env.payload["tag"]
        'INTERESADO'
    """
    return EventEnvelope(
        event_type=event_type_name(event),
        payload=event_to_dict(event),
        source_plugin=source_plugin,
        source_worker=source_worker,
    )
