"""Backfill del scorecard (HU-SC-1): califica con checks de código los
episodios cerrados que todavía no tienen scorecard. Idempotente."""
from __future__ import annotations

import json
from pathlib import Path

from src.plugins.chats.agent.sales_eval.scorecard import store
from src.plugins.chats.agent.sales_eval.scorecard.backfill import backfill_scorecards
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext


def _session(vault: Path, sid: str, episodes: list[dict], events: list[dict]) -> None:
    (vault / sid / "sessions").mkdir(parents=True)
    (vault / sid / "metadata.json").write_text(json.dumps({"episodes": episodes}), encoding="utf-8")
    (vault / sid / "sessions" / f"{sid}.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )


def _events(n: int) -> list[dict]:
    out = []
    for i in range(n):
        out.append({"role": "user", "content": f"mensaje {i}"})
        out.append({"role": "assistant", "content": f"respuesta {i}"})
    return out


def test_backfill_scores_closed_episodes_once(tmp_path: Path) -> None:
    _session(tmp_path, "wa_570000000001",
             [{"episode_id": "ep_001", "msgs_count_at_start": 0, "msgs_count_at_close": 8, "closed_at_ms": 1},
              {"episode_id": "ep_002", "msgs_count_at_start": 8}],  # abierto: no entra
             _events(6))

    first = backfill_scorecards(tmp_path, ctx=CheckContext())
    second = backfill_scorecards(tmp_path, ctx=CheckContext())

    assert (first.scored, first.skipped_existing) == (1, 0)
    assert (second.scored, second.skipped_existing) == (0, 1)
    found = store.find_latest(store.scorecards_dir(tmp_path), "wa_570000000001", "ep_001")
    assert found is not None and found["fidelity"] == "legacy"


def test_backfill_dry_run_writes_nothing(tmp_path: Path) -> None:
    _session(tmp_path, "wa_570000000002",
             [{"episode_id": "ep_001", "msgs_count_at_start": 0, "msgs_count_at_close": 8, "closed_at_ms": 1}],
             _events(6))

    result = backfill_scorecards(tmp_path, ctx=CheckContext(), dry_run=True)

    assert result.scored == 1
    assert store.find_latest(store.scorecards_dir(tmp_path), "wa_570000000002", "ep_001") is None
