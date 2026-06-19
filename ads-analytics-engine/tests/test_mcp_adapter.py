"""Golden tests for the official Meta Ads MCP adapter.

The official MCP (`ads_get_ad_entities`) returns human-formatted strings, not raw
numbers: `amount_spent: "$ 896.823 COP"`, `results: {"value": "205 (Messaging
conversations started)"}`, `actions:link_click: "571"` or `"Not available"`. The
adapter parses that deterministically (no LLM) into typed, objective-aware rows.

Values are from a real pull of the Hubara account (2026-06-19), hand-verified.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from ads_engine.meta_mcp import funnels_from_mcp, parse_ad_entities
from ads_engine.models import Recommendation
from ads_engine.report import render_mcp_markdown

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def payload():
    return json.loads((FIX / "mcp_ad_entities.json").read_text(encoding="utf-8"))


@pytest.mark.golden
def test_parses_messaging_campaign(payload):
    by = {r.campaign_id: r for r in parse_ad_entities(payload)}
    duo = by["120238728477970317"]
    assert duo.campaign_name == "Duo zodiacal"
    assert duo.objective == "OUTCOME_ENGAGEMENT"
    assert duo.spend_cop == 896823  # "$ 896.823 COP"
    assert duo.link_clicks == 571
    assert duo.result_count == 205  # "205 (Messaging conversations started)"
    assert duo.result_type == "Messaging conversations started"
    assert duo.cost_per_result_cop == 4375  # "$ 4.375 COP (...)"
    assert duo.is_messaging is True


@pytest.mark.golden
def test_parses_sales_campaign(payload):
    by = {r.campaign_id: r for r in parse_ad_entities(payload)}
    padre = by["120243118818600317"]
    assert padre.objective == "OUTCOME_SALES"
    assert padre.spend_cop == 239433
    assert padre.link_clicks == 446
    assert padre.result_count == 0
    assert padre.result_type == "Meta purchases"
    assert padre.is_messaging is False  # NOT a CTWA funnel campaign


@pytest.mark.golden
def test_handles_not_available_and_zero_spend(payload):
    by = {r.campaign_id: r for r in parse_ad_entities(payload)}
    madre = by["120240351877200317"]
    assert madre.spend_cop == 0
    assert madre.link_clicks == 0  # "Not available" → 0


def test_accepts_already_parsed_entities_list(payload):
    # parse_ad_entities should accept the raw MCP dict (ad_entities is a JSON string).
    rows = parse_ad_entities(payload)
    assert len(rows) == 3


@pytest.mark.golden
def test_funnels_only_messaging_campaigns(payload):
    funnels = funnels_from_mcp(parse_ad_entities(payload))
    ids = [f.campaign_id for f in funnels]
    assert "120238728477970317" in ids  # Duo zodiacal (messaging)
    assert "120243118818600317" not in ids  # Día del padre (OUTCOME_SALES) excluded

    duo = next(f for f in funnels if f.campaign_id == "120238728477970317")
    # 1 - 205/571 = 0.6410..., 896823/205 = 4374.7 → 4375 COP (matches Meta's cost_per_result)
    assert duo.drop_off_rate.quantize(Decimal("0.001")) == Decimal("0.641")
    assert duo.cost_per_conversation_cop.quantize(Decimal("1")) == Decimal("4375")
    assert duo.high_friction is True
    assert duo.recommendation is Recommendation.ROTATE_CREATIVE


@pytest.mark.golden
def test_render_shows_messaging_and_other_objectives(payload):
    md = render_mcp_markdown(parse_ad_entities(payload))
    assert "Duo zodiacal" in md
    assert "### Otras campañas" in md
    assert "Día del padre 2026 - mundia" in md
    assert "OUTCOME_SALES" in md
    assert "Gasto total de la cuenta" in md
