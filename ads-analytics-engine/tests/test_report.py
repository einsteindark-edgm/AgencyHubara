"""End-to-end golden test over the fixtures + a store round-trip.

Ties the whole pipeline together: parse Meta insights → ingest sales → merge →
metrics → report, asserting EXACT values, and proves the SQLite store preserves
them (because metrics are recomputed deterministically on the way out).
"""

from pathlib import Path

import pytest

from ads_engine.ingest import load_manual_sales
from ads_engine.merge import merge
from ads_engine.meta_insights import load_meta_insights
from ads_engine.report import render_markdown, to_dict
from ads_engine.store import Store

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def result():
    insights = load_meta_insights(FIX / "meta_insights.json")
    sales = load_manual_sales(FIX / "manual_sales.json")
    return merge(insights, sales)


@pytest.mark.golden
def test_day_level_exact_values(result):
    days = {row["date"]: row for row in to_dict(result)["days"]}

    m1 = days["2026-06-01"]["metrics"]
    assert m1["drop_off_rate"] == "0.8"
    assert m1["cost_per_conversation_cop"] == "3000"
    assert m1["mer"] == "1.5"
    assert m1["global_cpa_cop"] == "20000"
    assert m1["global_win_rate"] == "0.15"
    assert days["2026-06-01"]["diagnosis"]["recommendation"] == "rotate_creative"

    # Edge day: no clicks / no conversations / no orders → undefined, never invented.
    m3 = days["2026-06-03"]["metrics"]
    assert m3["drop_off_rate"] is None
    assert m3["cost_per_conversation_cop"] is None
    assert m3["global_cpa_cop"] is None
    assert m3["mer"] == "0"  # zero revenue over real spend
    assert days["2026-06-03"]["diagnosis"]["recommendation"] == "insufficient_data"


@pytest.mark.golden
def test_period_totals_exact(result):
    period = to_dict(result)["period"]
    assert period["spend_cop"] == 600000
    assert period["total_revenue_cop"] == 690000
    assert period["metrics"]["mer"] == "1.15"
    assert period["metrics"]["cost_per_conversation_cop"] == "1500"
    assert period["metrics"]["global_win_rate"] == "0.0575"
    assert period["diagnosis"]["recommendation"] == "rotate_creative"


@pytest.mark.golden
def test_markdown_contains_key_cells(result):
    md = render_markdown(result)
    assert "## Hubara" in md
    assert "80.0%" in md  # day1 drop-off
    assert "$3.000" in md  # day1 cost/conversation (COP thousands sep)
    assert "1.50" in md  # day1 MER
    assert "rotate_creative" in md
    assert "1.15" in md  # period MER


@pytest.mark.functional
def test_store_roundtrip_preserves_numbers(tmp_path, result):
    store = Store(tmp_path / "t.db")
    store.replace_blended(result)
    reloaded = store.load_blended()
    assert to_dict(reloaded)["days"] == to_dict(result)["days"]
    assert to_dict(reloaded)["period"] == to_dict(result)["period"]
