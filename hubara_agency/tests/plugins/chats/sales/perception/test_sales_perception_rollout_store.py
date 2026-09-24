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


PROFILE = "jev-v1"


def _trace(sid_dir: Path, *, at: int, mode: str, fallback: str | None, dur: int,
           latency: int | None = None, profile: str = PROFILE) -> None:
    sid_dir.mkdir(parents=True, exist_ok=True)
    step = {"kind": "perception", "profile": profile, "fallback": fallback, "dur_ms": dur}
    if latency is not None:
        step["latency_ms"] = latency
    line = {"turn_started_ms": at, "mode": mode, "steps": [step, {"kind": "llm"}]}
    with (sid_dir / "turn_traces.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


def test_shadow_metrics_come_from_the_turn_traces(tmp_path: Path) -> None:
    """"7 días en sombra" son 7 días DISTINTOS con turnos medidos: una prueba
    suelta de hace 8 días y un turno de ayer son 2 días, no 8."""
    a = tmp_path / "wa_573001234567" / "evals"
    b = tmp_path / "wa_573007654321" / "evals"
    for i in range(98):
        _trace(a if i % 2 else b, at=NOW - 8 * DAY + i * 60_000, mode="shadow", fallback=None, dur=300 + i)
    _trace(a, at=NOW - DAY, mode="shadow", fallback="timeout", dur=3000)
    _trace(a, at=NOW - DAY, mode="off", fallback=None, dur=0)
    _trace(tmp_path / "_evals" / "x", at=NOW, mode="shadow", fallback=None, dur=1)  # directorio _*: no es sesión

    m = shadow_metrics(tmp_path, now_ms=NOW, profile=PROFILE)

    assert m.turns == 99 and m.days == 2
    assert abs(m.fallback_rate - 1 / 99) < 1e-9
    assert 380 <= m.p95_ms <= 400


def test_the_evidence_is_recent_real_and_from_the_current_profile(tmp_path: Path) -> None:
    """La vara se mide con lo que va a actuar: el perfil vigente (si Terraform
    cambia de Jev a OpenAI, la sombra empieza de cero), las últimas dos
    semanas (lo viejo se olvida) y sin las sesiones de la suite golden."""
    s = tmp_path / "wa_573001234567" / "evals"
    _trace(s, at=NOW - 20 * DAY, mode="shadow", fallback="timeout", dur=9000)  # fuera de la ventana
    _trace(s, at=NOW - DAY, mode="shadow", fallback="timeout", dur=9000, profile="openai-lp-v1")  # otro perfil
    _trace(tmp_path / "wa_golden_x" / "evals", at=NOW - DAY, mode="shadow", fallback="timeout", dur=9000)
    _trace(s, at=NOW - DAY, mode="shadow", fallback=None, dur=400)

    m = shadow_metrics(tmp_path, now_ms=NOW, profile=PROFILE)

    assert (m.days, m.turns, m.fallback_rate, m.p95_ms) == (1, 1, 0.0, 400)


def test_the_p95_is_the_classifier_latency(tmp_path: Path) -> None:
    """En sombra el paso se lee después de enviar: si la traza trae la latencia
    del clasificador (`latency_ms`), esa es la que cuenta."""
    _trace(tmp_path / "wa_573001234567" / "evals", at=NOW - DAY, mode="shadow", fallback=None, dur=15_000, latency=420)

    assert shadow_metrics(tmp_path, now_ms=NOW, profile=PROFILE).p95_ms == 420


def test_a_session_untouched_in_the_window_is_not_even_read(tmp_path: Path) -> None:
    """El recorrido es por cada GET y PUT del panel: solo se leen las sesiones
    con trazas escritas dentro de la ventana."""
    import os

    s = tmp_path / "wa_573001234567" / "evals"
    _trace(s, at=NOW - DAY, mode="shadow", fallback=None, dur=400)
    old = (NOW - 30 * DAY) / 1000
    os.utime(s / "turn_traces.jsonl", (old, old))

    assert shadow_metrics(tmp_path, now_ms=NOW, profile=PROFILE).turns == 0


def test_no_shadow_yet(tmp_path: Path) -> None:
    m = shadow_metrics(tmp_path, now_ms=NOW, profile=PROFILE)

    assert (m.days, m.turns, m.fallback_rate, m.p95_ms) == (0, 0, None, None)
