"""Traducción de `window` → query del backend, igual que el dashboard.

Fuente de verdad: `src/shared/window.ts` del plugin y `AdsSection.tsx:72-79`.
"""

import pytest

from hubara_gateway.window import WindowError, parse_window, window_query


@pytest.mark.parametrize(("preset", "days"), [("7d", "7"), ("30d", "30"), ("90d", "90")])
def test_window_query_preset_sends_days(preset: str, days: str) -> None:
    assert window_query(parse_window(preset)) == {"days": days}


def test_window_query_total_sends_nothing() -> None:
    assert window_query(parse_window("total")) == {}


def test_window_query_total_in_daily_sends_90_days() -> None:
    assert window_query(parse_window("total"), daily=True) == {"days": "90"}


def test_parse_window_missing_defaults_to_30d() -> None:
    assert parse_window(None) == "30d"
    assert parse_window("") == "30d"


def test_window_query_range_sends_from_to() -> None:
    w = parse_window("2026-09-01..2026-09-30")
    assert window_query(w) == {"from": "2026-09-01", "to": "2026-09-30"}
    # El rango gana también en daily (el dashboard manda from/to tal cual).
    assert window_query(w, daily=True) == {"from": "2026-09-01", "to": "2026-09-30"}


def test_parse_window_inverted_range_is_normalized() -> None:
    assert parse_window("2026-09-30..2026-09-01") == "2026-09-01..2026-09-30"


@pytest.mark.parametrize(
    "raw",
    ["14d", "yesterday", "2026-09-01", "2026-09-01..", "2026-13-01..2026-09-30", "2026-02-30..2026-03-01", "x..y"],
)
def test_parse_window_invalid_raises(raw: str) -> None:
    with pytest.raises(WindowError):
        parse_window(raw)
