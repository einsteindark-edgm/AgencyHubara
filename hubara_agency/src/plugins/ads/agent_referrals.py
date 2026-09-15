"""Conversations an AI agent sent to WhatsApp, for the ads dashboard.

The storefront tags its WhatsApp button with `via: chatgpt` when the visit came
from an agent (hubara_frontend, `whatsapp-handoff.ts`). The chats ingest appends
each one to `metadata.agent_referrals` (one entry per source per episode) —
that log is the data channel; this module only reads it, so ads imports nothing
from chats.

Counted by the referral's own timestamp, with the same window contract as the
campaigns endpoint (`since` inclusive, `until` exclusive). `with_order` is the
number that matters: how many of those conversations became a sale.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# Closed list, mirrored from the storefront's `AgentSource` and the chats
# ingest. `via:` is typed by whoever sends the message: anything else is noise.
AGENT_SOURCES: tuple[str, ...] = ("chatgpt", "gemini", "perplexity", "copilot", "claude")


@dataclass(frozen=True)
class AgentReferralSummary:
    total: int = 0
    with_order: int = 0
    by_source: dict[str, int] = field(default_factory=dict)


def _episodes_with_order(metadata: dict[str, Any]) -> set[str]:
    episodes = metadata.get("episodes")
    if not isinstance(episodes, list):
        return set()
    return {
        ep["episode_id"]
        for ep in episodes
        if isinstance(ep, dict) and ep.get("episode_id") and ep.get("order_id")
    }


def count_agent_referrals(
    sessions: Iterable[tuple[Path, dict[str, Any]]],
    *,
    since_ms: int | None = None,
    until_ms: int | None = None,
) -> AgentReferralSummary:
    total = 0
    with_order = 0
    by_source: dict[str, int] = {}

    for _session_dir, metadata in sessions:
        referrals = metadata.get("agent_referrals")
        if not isinstance(referrals, list):
            continue
        ordered = _episodes_with_order(metadata)

        for entry in referrals:
            if not isinstance(entry, dict):
                continue
            source = entry.get("source")
            at_ms = entry.get("at_ms")
            if source not in AGENT_SOURCES or not isinstance(at_ms, int):
                continue
            if since_ms is not None and at_ms < since_ms:
                continue
            if until_ms is not None and at_ms >= until_ms:
                continue

            total += 1
            by_source[source] = by_source.get(source, 0) + 1
            if entry.get("episode_id") in ordered:
                with_order += 1

    return AgentReferralSummary(total=total, with_order=with_order, by_source=by_source)
