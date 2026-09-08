"""Completion events emitted by the ``orders`` plugin.

These dataclasses are the **boundary type** between the ``orders`` plugin and
the ``chats`` plugin's ETA agent. When the operator transitions an order's
stage from the dashboard kanban (``PATCH /api/orders/orders/{id}/stage`` etc.),
the orders API emits an ``OrderStageChangedEvent`` → the dispatcher reads the
manifest ``transitions[]`` declared under the orders ``reconcile`` worker →
starts (``preparing``) or signals (``ready``/``shipping``/``delivered``/
``cancelled``) the ETA notification workflow that lives in the ``chats`` plugin.

The orders plugin never imports the ETA workflow class (R-DIP #10): the
dispatcher routes by name strings resolved from the manifest, and the target
workflow receives the payload as a plain dict.

Conventions (enforced by ``tests/architecture/test_manifest_orchestration_consistency.py``):

- ``@dataclass(frozen=True)`` — R-JSON, hashable, replay-safe.
- Class name ends with ``Event``.
- All fields are JSON-serializable (str / int / float / bool / None / nested
  frozen dataclasses, list/dict of those).
- ``session_id: str`` is the first field so the default
  ``workflow_id_template`` (``{event.session_id}``) resolves without override —
  though for ETA the manifest declares an explicit ``eta-{event.session_id}``
  template.

See ADR-2026-05-20 §3 and ``src.platform.orchestration.events`` for the
dispatcher contract.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OrderStageChangedEvent:
    """Emitted when an order's operational stage changes (human-driven from the
    dashboard kanban, or any code path that runs the order command port).

    The ``to_stage`` field drives the manifest transition. The orders manifest
    declares one transition per stage:

      - ``when: {to_stage: preparing|ready|shipping|delivered|cancelled}`` →
        ``signal_with_start`` ``notify_stage_change`` on the customer's ETA
        session (``eta-{session_id}``). If the session is running it receives
        the signal; if it never started or already closed (idle / proactive
        close) it starts fresh with that payload as seed. The ETA session is
        multi-order (tracking lives in ``metadata.eta_tracking.orders``), so a
        new order of the same customer JOINS the live session.
      - 2026-09-08: ``preparing`` used ``start_workflow_with_replace``, which
        TERMINATED the customer's live session on every new order (runs
        ``Terminated`` 01a07cd2 / 01a07e9f) and raced an in-flight
        notification. Guard: ``tests/plugins/orders/test_manifest_eta_transitions.py``.

    Fields:
        session_id: the chats session id of the customer who owns this order
            (``wa_<phone>``). Resolved by the orders API via
            ``_resolve_session_for_order`` (canonical ``order.metadata.session_key``
            → reverse lookup by ``episodes[].order_id`` → shipping phone). The
            event is NOT emitted if the order can't be linked to a session.
        order_id: the Medusa order / draft id (``order_01...`` / ``draft_01...``).
            Travels to the ETA workflow so its activities can fetch the current
            order facts (customer name, total, payment type, scheduled window)
            from the order query port at notification time.
        to_stage: the new operational stage. One of ``preparing`` / ``ready`` /
            ``shipping`` / ``delivered`` / ``cancelled`` (the closed-list from
            ``src.platform.orders.state.STAGE_VALUES`` minus ``new``). Matched
            literally by the manifest ``when:`` clauses.
        occurred_at_ms: epoch ms when the transition happened. Carried for
            observability + ordering; not used for routing.
        tracking_url: optional carrier tracking link the operator attached
            when moving the order to ``shipping``. Mapped by the manifest
            (``$.tracking_url``) into the ETA ``notify_stage_change`` payload
            so the WhatsApp message that announces "en camino" carries the
            link. ``None`` for every other stage / legacy emitters.
    """

    session_id: str
    order_id: str
    to_stage: str
    occurred_at_ms: int = 0
    tracking_url: str | None = None


__all__ = ["OrderStageChangedEvent"]
