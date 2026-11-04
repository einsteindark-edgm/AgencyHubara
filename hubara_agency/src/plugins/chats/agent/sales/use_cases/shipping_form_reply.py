"""The shipping form's data go into the order the moment they arrive (ENV-03).

Production gaps report (2026-11-03, «Selección y formulario escritos al
llegar»): the form reply (`nfm_reply`) arrives structured — city, address,
phone, who receives it, payment method — but it only reached the order draft
if the model called `set_order_slot` in that turn. Those are facts, not a
decision: the ingest writes them into the draft of the active episode, the
same way it writes the web cart, and the model reads them in the order note.

PURE: mutates the `metadata` it receives; the caller persists it (RMW under the
store lock). Not written when the episode already has a registered order: the
order is the source of truth from then on (`get_projectable_draft`).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import get_active_episode
from src.plugins.chats.agent.sales.use_cases.order_draft import update_order_draft
from src.plugins.chats.shared.operator_order import form_reply_slots


def record_form_reply(metadata: dict[str, Any], response_json: Any, *, now_ms: int) -> dict[str, str]:
    """Writes the form's data into the active episode's draft. Returns the
    slots written (`{}`: nothing to write, no active episode or an order
    already registered). A field left blank does not erase what the draft had."""
    if not isinstance(response_json, Mapping):
        return {}
    episode = get_active_episode(metadata)
    if episode is None or episode.get("order_id"):
        return {}
    slots = form_reply_slots(response_json)
    if slots:
        update_order_draft(metadata, slots=slots, now_ms=now_ms)
    return slots
