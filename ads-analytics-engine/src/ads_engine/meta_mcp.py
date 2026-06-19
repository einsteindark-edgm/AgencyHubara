"""Adapter for the official Meta Ads MCP `ads_get_ad_entities` output.

The official MCP returns human-formatted display strings, not raw numbers:
`amount_spent: "$ 896.823 COP"`, `results: {"value": "205 (Messaging
conversations started)"}`, `actions:link_click: "571"` or `"Not available"`.
This module parses them deterministically (no LLM, no Graph API call — the agent
calls the MCP and hands us the JSON) into typed, objective-aware rows.

`results` is objective-dependent: messaging campaigns report "Messaging
conversations started", sales campaigns report "Meta purchases", etc. Only
messaging campaigns are CTWA funnel campaigns (`is_messaging`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .campaigns import diagnose_funnel
from .metrics import cost_per_conversation_cop, drop_off_rate
from .models import CampaignFunnel

# `results` label that marks a real WhatsApp CTWA conversation campaign.
_MESSAGING_RESULT_LABEL = "messaging conversations started"
_NOT_AVAILABLE = "not available"
_DIGITS = re.compile(r"\d+")


@dataclass(frozen=True)
class MetaCampaign:
    """One campaign as reported by the official MCP (period aggregate)."""

    campaign_id: str
    campaign_name: str
    objective: str
    spend_cop: int
    link_clicks: int
    result_count: int
    result_type: str  # e.g. "Messaging conversations started", "Meta purchases"
    cost_per_result_cop: int | None

    @property
    def is_messaging(self) -> bool:
        """True only when the campaign's result really is WhatsApp conversations (CTWA)."""
        return _MESSAGING_RESULT_LABEL in (self.result_type or "").lower()


def _parse_cop(text: object) -> int:
    """'$ 896.823 COP' / '$ 4.375 COP (label)' → 896823. Blank/NA → 0.

    COP has no decimal places, so every dot is a thousands separator — strip all
    non-digits from the amount (before any '(label)' suffix).
    """
    head = str(text or "").split("(")[0]
    if _NOT_AVAILABLE in head.lower():
        return 0
    digits = re.sub(r"[^\d]", "", head)
    return int(digits) if digits else 0


def _parse_count(text: object) -> int:
    """'571' → 571 ; 'Not available' / '' → 0."""
    s = str(text or "")
    if _NOT_AVAILABLE in s.lower():
        return 0
    m = _DIGITS.search(s)
    return int(m.group()) if m else 0


def _parse_result(value: object) -> tuple[int, str]:
    """'205 (Messaging conversations started)' → (205, 'Messaging conversations started')."""
    s = str(value or "")
    count_match = _DIGITS.search(s)
    count = int(count_match.group()) if count_match else 0
    label_match = re.search(r"\(([^)]*)\)", s)
    label = label_match.group(1).strip() if label_match else ""
    return count, label


def _entity_to_campaign(entity: dict) -> MetaCampaign:
    result_count, result_type = _parse_result((entity.get("results") or {}).get("value"))
    cpr_raw = (entity.get("cost_per_result") or {}).get("value")
    cost_per_result = None
    if cpr_raw is not None and _NOT_AVAILABLE not in str(cpr_raw).lower():
        cost_per_result = _parse_cop(cpr_raw)
    return MetaCampaign(
        campaign_id=str(entity.get("id", "")),
        campaign_name=str(entity.get("name", "")),
        objective=str(entity.get("objective", "")),
        spend_cop=_parse_cop(entity.get("amount_spent")),
        link_clicks=_parse_count(entity.get("actions:link_click")),
        result_count=result_count,
        result_type=result_type,
        cost_per_result_cop=cost_per_result,
    )


def parse_ad_entities(payload: object) -> list[MetaCampaign]:
    """Parse the dict returned by the MCP `ads_get_ad_entities` tool.

    `payload["ad_entities"]` is itself a JSON-encoded string, so we double-decode.
    Also accepts an already-decoded list of entity dicts or a JSON string.
    """
    if isinstance(payload, (str, bytes)):
        payload = json.loads(payload)
    entities = payload.get("ad_entities", payload) if isinstance(payload, dict) else payload
    if isinstance(entities, (str, bytes)):
        entities = json.loads(entities)
    return [_entity_to_campaign(e) for e in entities]


def funnels_from_mcp(campaigns: list[MetaCampaign]) -> list[CampaignFunnel]:
    """Per-campaign CTWA funnel for MESSAGING campaigns only (drop-off, cost/conv).

    Non-messaging campaigns (sales, traffic) are skipped here — their results aren't
    WhatsApp conversations, so they belong in the report's 'other objectives' section.
    Reuses the same metric + diagnosis functions as the rest of the engine.
    """
    funnels = []
    for c in campaigns:
        if not c.is_messaging:
            continue
        drop = drop_off_rate(c.link_clicks, c.result_count)
        high_friction, recommendation = diagnose_funnel(drop)
        funnels.append(
            CampaignFunnel(
                campaign_id=c.campaign_id,
                campaign_name=c.campaign_name,
                spend_cop=c.spend_cop,
                inline_link_clicks=c.link_clicks,
                messaging_conversations_started=c.result_count,
                drop_off_rate=drop,
                cost_per_conversation_cop=cost_per_conversation_cop(c.spend_cop, c.result_count),
                high_friction=high_friction,
                recommendation=recommendation,
            )
        )
    funnels.sort(key=lambda f: (-f.spend_cop, f.campaign_id))
    return funnels
