"""DeepSeek, Jev and Gemini cards, all from the costs recorded per conversation."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

from src.plugins.costs.domain.period import DateRange
from src.plugins.costs.domain.report import Source, Status
from src.plugins.costs.providers.conversations import (
    ConversationCosts,
    ConversationCostsReader,
    conversation_providers,
)

RANGE = DateRange(date(2026, 11, 2), date(2026, 11, 3))
NOW = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)


def _costs() -> ConversationCosts:
    c = ConversationCosts()
    c.add("llm", date(2026, 11, 2), 125_000, 9_000)
    c.add("llm", date(2026, 11, 3), 500_000, 1_000)
    c.add("jev", date(2026, 11, 2), 2_000, 4)
    c.add("vision", date(2026, 11, 2), 150, 1)
    c.add("audio", date(2026, 11, 3), 300, 2)
    c.conversations = 7
    return c


class CountingScan:
    def __init__(self, result: ConversationCosts) -> None:
        self.calls = 0
        self.result = result

    def __call__(self, vault_dir: Path, rng: DateRange) -> ConversationCosts:
        self.calls += 1
        return self.result


def _cards(scan) -> dict:
    reader = ConversationCostsReader(Path("/vault"), scan=scan)
    return {p.id: p for p in conversation_providers(reader)}


def _fetch(provider):
    async def go():
        async with httpx.AsyncClient() as client:
            return await provider.fetch(client, RANGE, NOW)

    return asyncio.run(go())


def test_three_cards_in_a_fixed_order() -> None:
    assert list(_cards(CountingScan(_costs()))) == ["deepseek", "openrouter", "gemini"]


def test_deepseek_card_is_the_bots_llm_with_its_tokens() -> None:
    report = _fetch(_cards(CountingScan(_costs()))["deepseek"])

    assert report.status is Status.OK
    assert report.source is Source.CONVERSATIONS
    (line,) = report.lines
    assert line.currency == "USD"
    assert line.daily == {date(2026, 11, 2): 125_000, date(2026, 11, 3): 500_000}
    assert (line.quantity, line.unit) == (10_000, "tokens")
    assert any("7 conversaciones" in n for n in report.notes)


def test_jev_card_counts_its_questions() -> None:
    report = _fetch(_cards(CountingScan(_costs()))["openrouter"])

    (line,) = report.lines
    assert line.amount_micros == 2_000
    assert (line.quantity, line.unit) == (4, "preguntas")


def test_gemini_card_splits_photos_and_voice_notes() -> None:
    report = _fetch(_cards(CountingScan(_costs()))["gemini"])

    by = {ln.key: ln for ln in report.lines}
    assert by["vision"].amount_micros == 150
    assert by["audio"].amount_micros == 300
    assert by["audio"].quantity == 2


def test_no_spend_in_the_range_is_a_real_zero() -> None:
    report = _fetch(_cards(CountingScan(ConversationCosts()))["openrouter"])

    assert report.status is Status.OK
    assert report.lines == ()


def test_the_three_cards_share_one_read_of_the_vault() -> None:
    scan = CountingScan(_costs())
    cards = _cards(scan)

    async def go():
        async with httpx.AsyncClient() as client:
            await asyncio.gather(*(c.fetch(client, RANGE, NOW) for c in cards.values()))

    asyncio.run(go())
    assert scan.calls == 1


def test_without_a_vault_the_cards_say_so_instead_of_zero() -> None:
    empty = ConversationCosts()
    empty.vault_found = False

    report = _fetch(_cards(CountingScan(empty))["deepseek"])

    assert report.status is Status.NOT_CONNECTED
    assert "conversaciones" in (report.how_to_connect or "")
