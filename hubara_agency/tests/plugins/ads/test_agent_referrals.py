"""Conversations an AI agent sent (ChatGPT, Gemini…), for the ads dashboard.

The storefront tags its WhatsApp button with `via: chatgpt`; the chats ingest
appends each referral to `metadata.agent_referrals` (one per source per
episode). This use case counts them over the dashboard's date window, and how
many of those episodes turned into an order: the number that says whether
publishing the catalog for agents is actually selling.
"""
from __future__ import annotations

from pathlib import Path

from src.plugins.ads.agent_referrals import count_agent_referrals

_DAY = 86_400_000


def _session(referrals: list[dict], episodes: list[dict] | None = None):
    return (Path("/vault/wa_1"), {"agent_referrals": referrals, "episodes": episodes or []})


def test_counts_referrals_by_agent():
    summary = count_agent_referrals(
        [
            _session([{"source": "chatgpt", "sku": "HUB-CISNE", "episode_id": "ep_1", "at_ms": 1}]),
            _session(
                [
                    {"source": "chatgpt", "sku": "HUB-LOVE", "episode_id": "ep_1", "at_ms": 2},
                    {"source": "gemini", "sku": "HUB-LOVE", "episode_id": "ep_2", "at_ms": 3},
                ]
            ),
        ]
    )

    assert summary.total == 3
    assert summary.by_source == {"chatgpt": 2, "gemini": 1}


def test_counts_how_many_referred_episodes_ended_in_an_order():
    summary = count_agent_referrals(
        [
            _session(
                [
                    {"source": "chatgpt", "sku": "HUB-CISNE", "episode_id": "ep_1", "at_ms": 1},
                    {"source": "chatgpt", "sku": "HUB-LOVE", "episode_id": "ep_2", "at_ms": 2},
                ],
                episodes=[
                    {"episode_id": "ep_1", "order_id": "order_123"},
                    {"episode_id": "ep_2"},
                ],
            )
        ]
    )

    assert summary.with_order == 1


def test_only_counts_referrals_inside_the_window():
    """Same contract as the campaigns endpoint: `since` inclusive, `until`
    exclusive."""
    sessions = [
        _session(
            [
                {"source": "chatgpt", "sku": "A", "episode_id": "ep_1", "at_ms": 1 * _DAY},
                {"source": "chatgpt", "sku": "B", "episode_id": "ep_2", "at_ms": 2 * _DAY},
                {"source": "chatgpt", "sku": "C", "episode_id": "ep_3", "at_ms": 3 * _DAY},
            ]
        )
    ]

    summary = count_agent_referrals(sessions, since_ms=2 * _DAY, until_ms=3 * _DAY)

    assert summary.total == 1


def test_tolerates_sessions_without_referrals_and_malformed_entries():
    summary = count_agent_referrals(
        [
            (Path("/vault/wa_2"), {}),
            (Path("/vault/wa_3"), {"agent_referrals": "oops"}),
            _session([{"source": "chatgpt"}, "junk", {"source": "chatgpt", "at_ms": 5}]),
        ]
    )

    assert summary.total == 1
    assert summary.by_source == {"chatgpt": 1}


def test_ignores_a_source_outside_the_closed_list():
    """`via:` is typed by whoever sends the message; only the storefront's list
    is a real agent."""
    summary = count_agent_referrals(
        [_session([{"source": "instagram", "episode_id": "ep_1", "at_ms": 1}])]
    )

    assert summary.total == 0
