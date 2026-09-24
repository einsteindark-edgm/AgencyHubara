"""Estado del encendido y métricas de la sombra (plan del laboratorio §4.4 y
PR 16). El estado vive en `<vault>/_rollout/perception.json` (directorio `_*`:
los recorredores de sesiones lo ignoran). Las métricas salen de las trazas de
los turnos en sombra: días, turnos, caídas a "turno como hoy" y p95."""
from __future__ import annotations

import json
from pathlib import Path

from src.plugins.chats.agent.sales.perception.rollout import RolloutState
from src.plugins.chats.agent.sales.perception.rollout_store import (
    read_state,
    shadow_metrics,
    write_state,
)

DAY = 86_400_000
NOW = 1_790_200_000_000


def test_a_missing_or_broken_state_is_off(tmp_path: Path) -> None:
    assert read_state(tmp_path) == RolloutState()
    (tmp_path / "_rollout").mkdir()
    (tmp_path / "_rollout" / "perception.json").write_text("{no es json", encoding="utf-8")
    assert read_state(tmp_path) == RolloutState()


def test_the_state_round_trips(tmp_path: Path) -> None:
    state = RolloutState(mode="canary", canary_percent=10, test_numbers=("wa_573001234567",), updated_at_ms=NOW, updated_by="operador")

    write_state(tmp_path, state)

    assert read_state(tmp_path) == state
    assert not list((tmp_path / "_rollout").glob("*.tmp"))


def _trace(sid_dir: Path, *, at: int, mode: str, fallback: str | None, dur: int) -> None:
    sid_dir.mkdir(parents=True, exist_ok=True)
    line = {"turn_started_ms": at, "mode": mode, "steps": [
        {"kind": "perception", "fallback": fallback, "dur_ms": dur}, {"kind": "llm"},
    ]}
    with (sid_dir / "turn_traces.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


def test_shadow_metrics_come_from_the_turn_traces(tmp_path: Path) -> None:
    a = tmp_path / "wa_573001234567" / "evals"
    b = tmp_path / "wa_573007654321" / "evals"
    for i in range(98):
        _trace(a if i % 2 else b, at=NOW - 8 * DAY + i * 60_000, mode="shadow", fallback=None, dur=300 + i)
    _trace(a, at=NOW - DAY, mode="shadow", fallback="timeout", dur=3000)
    _trace(a, at=NOW - DAY, mode="off", fallback=None, dur=0)
    _trace(tmp_path / "_evals" / "x", at=NOW, mode="shadow", fallback=None, dur=1)  # directorio _*: no es sesión

    m = shadow_metrics(tmp_path, now_ms=NOW)

    assert m.turns == 99 and m.days == 8
    assert abs(m.fallback_rate - 1 / 99) < 1e-9
    assert 380 <= m.p95_ms <= 400


def test_no_shadow_yet(tmp_path: Path) -> None:
    m = shadow_metrics(tmp_path, now_ms=NOW)

    assert (m.days, m.turns, m.fallback_rate, m.p95_ms) == (0, 0, None, None)
