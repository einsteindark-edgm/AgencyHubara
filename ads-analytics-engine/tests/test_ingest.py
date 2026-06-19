"""Tests for manual-sales ingestion (JSON + CSV, validation)."""

from datetime import date
from pathlib import Path

import pytest

from ads_engine.ingest import load_manual_sales
from ads_engine.models import ManualSale

FIX = Path(__file__).parent / "fixtures"


def test_load_json():
    sales = load_manual_sales(FIX / "manual_sales.json")
    assert len(sales) == 3
    assert sales[0] == ManualSale(date=date(2026, 6, 1), total_orders=15, total_revenue_cop=450000)


def test_json_and_csv_agree():
    assert load_manual_sales(FIX / "manual_sales.json") == load_manual_sales(
        FIX / "manual_sales.csv"
    )


def test_total_revenue_alias_maps_to_cop():
    # The fixture uses the spec's 'total_revenue' key; it must land in total_revenue_cop.
    sales = load_manual_sales(FIX / "manual_sales.json")
    assert sales[0].total_revenue_cop == 450000


def test_bad_date_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('[{"date":"not-a-date","total_orders":1,"total_revenue":1}]', encoding="utf-8")
    with pytest.raises(ValueError):
        load_manual_sales(p)


def test_missing_revenue_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('[{"date":"2026-06-01","total_orders":1}]', encoding="utf-8")
    with pytest.raises(ValueError):
        load_manual_sales(p)
