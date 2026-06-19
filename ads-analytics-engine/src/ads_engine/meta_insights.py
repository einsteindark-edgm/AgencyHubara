"""Parse a raw Meta Ads insights payload into typed daily insights.

This is the tested seam between the (untrusted) data fetch and the deterministic
core. The ONLY supported data source is the official Meta Ads MCP: the agent
calls its ``get_insights`` tool and saves the raw response — a ``{"data": [...]}``
envelope with per-day rows — which this module parses. There is no direct Graph
API path (a raw-token Graph call can get the ad account banned).
"""

from __future__ import annotations

import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .models import CURRENCY, MetaDailyInsight

# The CTWA "messaging conversation started (7d)" action — as relayed by the Meta Ads MCP.
CTWA_ACTION_TYPE = "onsite_conversion.messaging_conversation_started_7d"


def _spend_to_int_cop(spend: object) -> int:
    # Meta returns spend as a string in account-currency major units.
    return int(Decimal(str(spend)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _conversations_from_actions(actions: object) -> int:
    for action in actions or []:
        if isinstance(action, dict) and action.get("action_type") == CTWA_ACTION_TYPE:
            return int(Decimal(str(action.get("value", 0))))
    return 0


def _row_to_insight(row: dict, account_currency: str) -> MetaDailyInsight:
    return MetaDailyInsight(
        date=date.fromisoformat(row["date_start"]),
        spend_cop=_spend_to_int_cop(row.get("spend", 0)),
        inline_link_clicks=int(Decimal(str(row.get("inline_link_clicks", 0)))),
        messaging_conversations_started=_conversations_from_actions(row.get("actions")),
        currency=account_currency,
        campaign_id=row.get("campaign_id"),
        campaign_name=row.get("campaign_name"),
    )


def parse_meta_insights(payload: object) -> list[MetaDailyInsight]:
    """payload = dict from Graph ``/insights`` (or the MCP tool), or its JSON string."""
    if isinstance(payload, (str, bytes)):
        payload = json.loads(payload)
    if isinstance(payload, dict):
        rows = payload.get("data", [])
        account_currency = payload.get("account_currency", CURRENCY)
    else:
        rows = payload
        account_currency = CURRENCY
    return [_row_to_insight(r, account_currency) for r in rows]


def load_meta_insights(path: str | Path) -> list[MetaDailyInsight]:
    return parse_meta_insights(Path(path).read_text(encoding="utf-8"))
