"""Golden tests for the per-campaign funnel breakdown.

Per-campaign we can ONLY measure the Meta-side funnel (drop-off, cost per
conversation) — revenue/MER/CPA stay account-level because manual WhatsApp sales
can't be deterministically attributed to a campaign. Values hand-computed.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from ads_engine.campaigns import campaign_breakdown, collapse_to_daily
from ads_engine.ingest import load_manual_sales
from ads_engine.merge import merge
from ads_engine.meta_insights import load_meta_insights, parse_meta_insights
from ads_engine.models import Recommendation
from ads_engine.report import render_markdown, to_dict

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def insights():
    return load_meta_insights(FIX / "meta_insights_campaigns.json")


@pytest.mark.golden
def test_parse_captures_campaign(insights):
    assert {i.campaign_id for i in insights} == {"c_aroma", "c_regalo"}
    aroma = [i for i in insights if i.campaign_id == "c_aroma"]
    assert {i.campaign_name for i in aroma} == {"Velas Aroma"}


@pytest.mark.golden
def test_campaign_breakdown_exact(insights):
    by = {c.campaign_id: c for c in campaign_breakdown(insights)}

    aroma = by["c_aroma"]
    assert aroma.spend_cop == 300000  # 200000 + 100000
    assert aroma.inline_link_clicks == 800  # 500 + 300
    assert aroma.messaging_conversations_started == 160  # 100 + 60
    assert aroma.drop_off_rate == Decimal("0.8")  # 1 - 160/800
    assert aroma.cost_per_conversation_cop == Decimal("1875")  # 300000/160
    assert aroma.high_friction is True
    assert aroma.recommendation is Recommendation.ROTATE_CREATIVE

    regalo = by["c_regalo"]
    assert regalo.spend_cop == 200000
    assert regalo.inline_link_clicks == 400
    assert regalo.messaging_conversations_started == 300
    assert regalo.drop_off_rate == Decimal("0.25")  # 1 - 300/400
    assert regalo.cost_per_conversation_cop.quantize(Decimal("0.01")) == Decimal("666.67")
    assert regalo.high_friction is False
    assert regalo.recommendation is Recommendation.FUNNEL_HEALTHY


@pytest.mark.golden
def test_breakdown_sorted_by_spend_desc(insights):
    assert [c.campaign_id for c in campaign_breakdown(insights)] == ["c_aroma", "c_regalo"]


@pytest.mark.golden
def test_collapse_to_daily_sums_across_campaigns(insights):
    daily = {d.date: d for d in collapse_to_daily(insights)}
    assert daily[date(2026, 6, 1)].spend_cop == 300000  # 200000 + 100000
    assert daily[date(2026, 6, 1)].inline_link_clicks == 700  # 500 + 200
    assert daily[date(2026, 6, 1)].messaging_conversations_started == 260  # 100 + 160
    assert daily[date(2026, 6, 2)].spend_cop == 200000
    assert all(d.campaign_id is None for d in daily.values())


def test_account_level_has_no_breakdown_and_collapses_identically():
    payload = {
        "account_currency": "COP",
        "data": [
            {
                "date_start": "2026-06-01", "date_stop": "2026-06-01",
                "spend": "1000", "inline_link_clicks": "10", "actions": [],
            }
        ],
    }
    ins = parse_meta_insights(payload)
    assert campaign_breakdown(ins) == []  # no campaign_id → nothing to break down
    assert collapse_to_daily(ins)[0].spend_cop == 1000


@pytest.mark.golden
def test_report_has_campaign_section_and_keeps_account_blended(insights):
    sales = load_manual_sales(FIX / "manual_sales.json")
    result = merge(collapse_to_daily(insights), sales)
    campaigns = campaign_breakdown(insights)

    md = render_markdown(result, campaigns)
    assert "### Por campaña" in md
    assert "Velas Aroma" in md
    assert "funnel_healthy" in md
    assert "### Cuenta (blended" in md  # account section still rendered

    payload = to_dict(result, campaigns)
    assert [c["campaign_id"] for c in payload["campaigns"]] == ["c_aroma", "c_regalo"]
    assert payload["campaigns"][1]["recommendation"] == "funnel_healthy"
    assert payload["period"]["metrics"]["mer"] is not None  # account profitability intact
