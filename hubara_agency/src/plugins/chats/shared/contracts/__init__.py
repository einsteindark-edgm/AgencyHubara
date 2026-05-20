"""Frozen DTOs shared between ``chats`` sub-agents.

Currently only ``events`` (completion events emitted by workflows for the
declarative orchestration dispatcher). Other cross-agent DTOs (transfer
decisions, scheduling payloads, etc.) MAY move here as the migration to
Level 3 proceeds — see ADR-2026-05-20.
"""

from src.plugins.chats.shared.contracts.events import (
    CustomerRepliedDuringRemarketingEvent,
    SalesSessionCompletionEvent,
)

__all__ = [
    "CustomerRepliedDuringRemarketingEvent",
    "SalesSessionCompletionEvent",
]
