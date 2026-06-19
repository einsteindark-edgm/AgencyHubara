"""Per-campaign funnel breakdown + collapse to account-level.

What can and cannot be measured per campaign:

- **Per campaign (Meta-side only):** spend, clicks, conversations, drop-off,
  cost per conversation. These come entirely from Meta, so they're attributable.
- **NOT per campaign:** revenue, MER, Global CPA, win rate. Manual WhatsApp sales
  can't be tied to a specific campaign (the whole attribution gap), so splitting
  revenue by campaign would be a fabricated number — we refuse to. Profitability
  stays account-level (see `merge` / `report`).

`collapse_to_daily` sums campaign-level rows into one account row per date, so the
account blend works regardless of whether the fetch was account- or campaign-level.
"""

from __future__ import annotations

from collections import OrderedDict

from .diagnosis import HIGH_FRICTION_DROP_OFF
from .metrics import cost_per_conversation_cop, drop_off_rate
from .models import CampaignFunnel, MetaDailyInsight, Recommendation


def collapse_to_daily(insights: list[MetaDailyInsight]) -> list[MetaDailyInsight]:
    """Sum across campaigns into one account-level row per date (campaign_id=None)."""
    totals: dict = OrderedDict()
    for i in insights:
        agg = totals.get(i.date)
        if agg is None:
            totals[i.date] = [i.spend_cop, i.inline_link_clicks, i.messaging_conversations_started]
        else:
            agg[0] += i.spend_cop
            agg[1] += i.inline_link_clicks
            agg[2] += i.messaging_conversations_started
    return [
        MetaDailyInsight(
            date=d,
            spend_cop=spend,
            inline_link_clicks=clicks,
            messaging_conversations_started=conv,
        )
        for d, (spend, clicks, conv) in sorted(totals.items())
    ]


def diagnose_funnel(drop_off: object) -> tuple[bool, Recommendation]:
    """Funnel-only verdict: friction drives creative rotation; profitability is elsewhere."""
    if drop_off is None:
        return (False, Recommendation.INSUFFICIENT_DATA)
    if drop_off > HIGH_FRICTION_DROP_OFF:
        return (True, Recommendation.ROTATE_CREATIVE)
    return (False, Recommendation.FUNNEL_HEALTHY)


def campaign_breakdown(insights: list[MetaDailyInsight]) -> list[CampaignFunnel]:
    """One CampaignFunnel per campaign, aggregated over the period, sorted by spend desc.

    Returns [] for account-level data (no campaign_id). Raises if the data mixes
    campaign-level and account-level rows (that's malformed — fail loudly).
    """
    with_campaign = [i for i in insights if i.campaign_id is not None]
    if not with_campaign:
        return []
    if len(with_campaign) != len(insights):
        raise ValueError("insights mix campaign-level and account-level rows; fetch one level")

    totals: dict = OrderedDict()
    names: dict = {}
    for i in with_campaign:
        agg = totals.get(i.campaign_id)
        if agg is None:
            totals[i.campaign_id] = [i.spend_cop, i.inline_link_clicks, i.messaging_conversations_started]
        else:
            agg[0] += i.spend_cop
            agg[1] += i.inline_link_clicks
            agg[2] += i.messaging_conversations_started
        names[i.campaign_id] = i.campaign_name or i.campaign_id

    funnels = []
    for campaign_id, (spend, clicks, conv) in totals.items():
        drop = drop_off_rate(clicks, conv)
        friction, recommendation = diagnose_funnel(drop)
        funnels.append(
            CampaignFunnel(
                campaign_id=campaign_id,
                campaign_name=names[campaign_id],
                spend_cop=spend,
                inline_link_clicks=clicks,
                messaging_conversations_started=conv,
                drop_off_rate=drop,
                cost_per_conversation_cop=cost_per_conversation_cop(spend, conv),
                high_friction=friction,
                recommendation=recommendation,
            )
        )
    funnels.sort(key=lambda c: (-c.spend_cop, c.campaign_id))
    return funnels
