"""La política de espera del turno de ventas ante una caída total del LLM
(incidente 2026-10-15): cuánto espera entre vueltas y cuándo se rinde."""
from __future__ import annotations

from datetime import timedelta

import pytest

from src.plugins.chats.agent.sales.contracts import ReplyWindow
from src.plugins.chats.agent.sales.workflows.llm_outage_v2 import (
    HUMAN_TOOK_OVER,
    WINDOW_CLOSING,
    outage_verdict,
    outage_wait,
)

_NOW = 1_791_000_000_000
_MIN = 60_000
_TEN = timedelta(minutes=10)


@pytest.mark.parametrize(("round_", "minutes"), [(1, 1), (2, 2), (3, 4), (4, 8), (5, 10), (40, 10)])
def test_the_wait_grows_and_stays_at_ten_minutes(round_: int, minutes: int) -> None:
    assert outage_wait(round_) == timedelta(minutes=minutes)


def test_it_waits_while_the_answer_still_fits_before_the_window_closes() -> None:
    window = ReplyWindow(route="ventas", window_expires_at_ms=_NOW + 16 * _MIN)

    assert outage_verdict(window, now_ms=_NOW, wait=_TEN, first_failure_ms=_NOW) is None


def test_it_gives_up_when_after_the_wait_the_answer_would_not_make_it() -> None:
    """Diez minutos de espera más los cinco que necesita un turno para salir."""
    window = ReplyWindow(route="ventas", window_expires_at_ms=_NOW + 14 * _MIN)

    assert outage_verdict(window, now_ms=_NOW, wait=_TEN, first_failure_ms=_NOW) == WINDOW_CLOSING


def test_without_a_known_window_it_waits_at_most_a_day_from_the_first_failure() -> None:
    window = ReplyWindow(route="ventas")
    first = _NOW - 23 * 60 * _MIN

    assert outage_verdict(window, now_ms=_NOW, wait=_TEN, first_failure_ms=first) is None
    assert outage_verdict(window, now_ms=_NOW + 50 * _MIN, wait=_TEN, first_failure_ms=first) == WINDOW_CLOSING


@pytest.mark.parametrize(("route", "verdict"), [("humano", HUMAN_TOOK_OVER), ("remarketing", None), ("ventas", None)])
def test_only_a_person_taking_the_chat_stops_the_wait(route: str, verdict: str | None) -> None:
    window = ReplyWindow(route=route, window_expires_at_ms=_NOW + 20 * 60 * _MIN)

    assert outage_verdict(window, now_ms=_NOW, wait=_TEN, first_failure_ms=_NOW) == verdict
