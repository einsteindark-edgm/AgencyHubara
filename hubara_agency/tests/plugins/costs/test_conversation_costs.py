"""Costs recorded in each conversation (vault episodes), summed by Bogotá day."""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

from src.plugins.costs.domain.period import DateRange
from src.plugins.costs.providers.conversations import scan_conversation_costs

RANGE = DateRange(date(2026, 11, 2), date(2026, 11, 3))
NOV2_10AM = int(datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc).timestamp() * 1000)
NOV3_23PM = int(datetime(2026, 11, 4, 4, 30, tzinfo=timezone.utc).timestamp() * 1000)  # 23:30 Bogotá
NOV4_1AM = int(datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc).timestamp() * 1000)
OCT30 = int(datetime(2026, 10, 30, 15, 0, tzinfo=timezone.utc).timestamp() * 1000)


def _episode(started_ms: int, **usage) -> dict:
    ep = {"episode_id": f"ep-{started_ms}", "started_at_ms": started_ms, "closed_at_ms": None}
    ep.update(usage)
    return ep


def _session(vault: Path, sid: str, episodes: list[dict]) -> Path:
    d = vault / sid
    d.mkdir(parents=True)
    (d / "metadata.json").write_text(json.dumps({"episodes": episodes}), encoding="utf-8")
    return d


def test_each_cost_field_is_summed_on_the_day_the_conversation_started(tmp_path) -> None:
    _session(tmp_path, "wa_573000000001", [
        _episode(NOV2_10AM,
                 llm_usage={"cost_usd": 0.125, "total_tokens": 9000},
                 jev_usage={"calls": 4, "cost_usd_micros": 2_000},
                 vision_usage={"calls": 1, "cost_usd_micros": 150},
                 audio_usage={"calls": 2, "cost_usd_micros": 300}),
    ])
    _session(tmp_path, "wa_573000000002", [
        _episode(OCT30, llm_usage={"cost_usd": 9.0, "total_tokens": 1}),  # before the range
        _episode(NOV3_23PM, llm_usage={"cost_usd": 0.5, "total_tokens": 1000}),
    ])

    costs = scan_conversation_costs(tmp_path, RANGE)

    assert costs.daily["llm"] == {date(2026, 11, 2): 125_000, date(2026, 11, 3): 500_000}
    assert costs.quantity["llm"] == 10_000  # tokens
    assert costs.daily["jev"] == {date(2026, 11, 2): 2_000}
    assert costs.quantity["jev"] == 4
    assert costs.daily["vision"] == {date(2026, 11, 2): 150}
    assert costs.daily["audio"] == {date(2026, 11, 2): 300}
    assert costs.quantity["audio"] == 2
    assert costs.conversations == 2


def test_conversations_after_the_range_are_left_out(tmp_path) -> None:
    _session(tmp_path, "wa_573000000003", [_episode(NOV4_1AM, llm_usage={"cost_usd": 1.0})])

    costs = scan_conversation_costs(tmp_path, RANGE)

    assert costs.daily["llm"] == {}
    assert costs.conversations == 0


def test_files_not_written_since_the_range_started_are_not_opened(tmp_path) -> None:
    old = _session(tmp_path, "wa_573000000004", [_episode(NOV2_10AM, llm_usage={"cost_usd": 1.0})])
    long_ago = datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()
    os.utime(old / "metadata.json", (long_ago, long_ago))

    costs = scan_conversation_costs(tmp_path, RANGE)

    assert costs.sessions_read == 0


def test_broken_files_and_foreign_folders_are_skipped(tmp_path) -> None:
    (tmp_path / "_analytics").mkdir()
    (tmp_path / "_analytics" / "metadata.json").write_text("{}", encoding="utf-8")
    bad = tmp_path / "wa_573000000005"
    bad.mkdir()
    (bad / "metadata.json").write_text("{not json", encoding="utf-8")
    _session(tmp_path, "wa_573000000099", [
        _episode(NOV2_10AM, llm_usage={"cost_usd": "oops"}, jev_usage={"calls": -1, "cost_usd_micros": None}),
        "not an episode",
    ])

    costs = scan_conversation_costs(tmp_path, RANGE)

    assert costs.daily["llm"] == {}
    assert costs.daily["jev"] == {}
    assert costs.skipped_files == 1


def test_missing_vault_reads_nothing(tmp_path) -> None:
    costs = scan_conversation_costs(tmp_path / "nope", RANGE)

    assert costs.vault_found is False
    assert costs.conversations == 0
