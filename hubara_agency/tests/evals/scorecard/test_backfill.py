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
    _session(tmp_path, "wa_100000000001",
             [{"episode_id": "ep_001", "msgs_count_at_start": 0, "msgs_count_at_close": 8, "closed_at_ms": 1},
              {"episode_id": "ep_002", "msgs_count_at_start": 8}],  # abierto: no entra
             _events(6))

    first = backfill_scorecards(tmp_path, ctx=CheckContext())
    second = backfill_scorecards(tmp_path, ctx=CheckContext())

    assert (first.scored, first.skipped_existing) == (1, 0)
    assert (second.scored, second.skipped_existing) == (0, 1)
    found = store.find_latest(store.scorecards_dir(tmp_path), "wa_100000000001", "ep_001")
    assert found is not None and found["fidelity"] == "legacy"


def test_backfill_dry_run_writes_nothing(tmp_path: Path) -> None:
    _session(tmp_path, "wa_100000000002",
             [{"episode_id": "ep_001", "msgs_count_at_start": 0, "msgs_count_at_close": 8, "closed_at_ms": 1}],
             _events(6))

    result = backfill_scorecards(tmp_path, ctx=CheckContext(), dry_run=True)

    assert result.scored == 1
    assert store.find_latest(store.scorecards_dir(tmp_path), "wa_100000000002", "ep_001") is None


def test_episodes_active_between_selects_real_episodes_overlapping_the_range(tmp_path: Path) -> None:
    """Re-calificar un rango (primer informe 9-15 sep): episodios que se
    solapan con el rango, abiertos o cerrados, con mensajes del cliente. Los
    `ep-seed-*` son del dataset sintético de prueba y no son clientes."""
    from src.plugins.chats.agent.sales_eval.scorecard.backfill import episodes_active_between

    start, end = 1_789_000_000_000, 1_789_600_000_000

    def session(sid: str, episodes: list[dict], events: list[dict]) -> None:
        d = tmp_path / sid / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        (tmp_path / sid / "metadata.json").write_text(json.dumps({"episodes": episodes}), encoding="utf-8")
        (d / f"{sid}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

    user = [{"role": "user", "content": "hola"}]
    three = [{"role": "user", "content": f"m{i}"} for i in range(3)]
    session("wa_100000000021", [
        {"episode_id": "ep_001", "started_at_ms": start - 5_000, "closed_at_ms": start - 1_000,   # antes
         "msgs_count_at_start": 0, "msgs_count_at_close": 1},
        {"episode_id": "ep_002", "started_at_ms": start - 1_000, "closed_at_ms": start + 1_000,   # cruza el inicio
         "msgs_count_at_start": 1, "msgs_count_at_close": 2},
        {"episode_id": "ep_003", "started_at_ms": end - 1_000, "msgs_count_at_start": 2},         # abierto
    ], three)
    session("wa_100000000022", [{"episode_id": "ep-seed-1783585415477", "started_at_ms": start,
                                  "msgs_count_at_start": 0}], user)
    session("wa_100000000023", [{"episode_id": "ep_001", "started_at_ms": end + 1, "msgs_count_at_start": 0}], user)
    session("wa_100000000024", [{"episode_id": "ep_001", "started_at_ms": start, "msgs_count_at_start": 0}],
            [{"role": "assistant", "content": "Hola de nuevo 🌿"}])                               # sin cliente

    units = episodes_active_between(tmp_path, start_ms=start, end_ms=end, now_ms=end + 10_000)

    assert units == [("wa_100000000021", "ep_002"), ("wa_100000000021", "ep_003")]


def test_rescore_script_range_is_bogota_days_inclusive() -> None:
    import importlib.util
    from datetime import datetime, timedelta, timezone

    spec = importlib.util.spec_from_file_location("rescore_scorecards", Path(__file__).resolve().parents[3] / "scripts" / "rescore_scorecards.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    start, end = mod.bogota_range_ms("2026-09-09", "2026-09-15")

    bogota = timezone(timedelta(hours=-5))
    assert datetime.fromtimestamp(start / 1000, bogota) == datetime(2026, 9, 9, tzinfo=bogota)
    assert datetime.fromtimestamp(end / 1000, bogota) == datetime(2026, 9, 16, tzinfo=bogota)
