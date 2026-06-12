"""EventKit — eventos declarativos + dispatcher genérico (canal 2).

La ÚNICA forma de comunicación cross-worker (ADR-2026-05-20): el workflow
emite un evento frozen JSON-safe; el manifest declara ``emits`` y
``transitions[]``; el dispatcher resuelve y arranca/señala el target SIN que
nadie importe la clase del workflow ajeno (R-DIP #10).

Uso canónico (en un workflow)::

    from src.sdk.eventkit import dispatch_event_activity, envelope_for

    envelope = envelope_for(MyCompletionEvent(...), plugin="orders", worker="sync")
    await workflow.execute_activity(dispatch_event_activity, envelope, ...)

Reglas que el TestKit enforcea sobre lo que pasa por acá:
- Todo evento es ``@dataclass(frozen=True)`` JSON-safe en
  ``plugins/<id>/shared/contracts/events.py`` (R-JSON).
- ``transitions[].on_event ∈ emits[]`` del mismo worker
  (orchestration-consistency).
- Hacia targets de vida finita: ``via: signal_with_start`` — NUNCA ``signal``
  pelado (lección L-8); el input_mapping debe cubrir el start input completo.
"""
from __future__ import annotations

from src.platform.orchestration import (
    DispatchResult as DispatchResult,
    EventEnvelope as EventEnvelope,
    Transition as Transition,
    TransitionAction as TransitionAction,
    dispatch_envelope_with_client as dispatch_envelope_with_client,
    dispatch_event_activity as dispatch_event_activity,
    envelope_for as envelope_for,
    event_get as event_get,
    event_to_dict as event_to_dict,
    event_type_name as event_type_name,
)
