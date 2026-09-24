"""Costo estimado y topes del botón "Nueva corrida" (plan §3.7 y §8.2).

Números del plan: el agente gastó US$7,09 en los 91 episodios reales (~404
turnos) y el juez ~US$30 en una corrida de decisión. Corrida de decisión
(A1, B y C × 3) ≈ US$95; corrida rápida (A1 y un bot nuevo × 1) ≈ US$20.
La API no deja lanzar lo que no cabe en el tope por corrida ni en lo que
queda del mes.
"""
from __future__ import annotations

import json

import pytest

from src.plugins.chats.agent.sales_lab.launch.costs import (
    check_caps,
    estimate_run_usd,
    month_spent_usd,
)
from src.sdk.labkit import FilesystemLabStore

SEP_23 = 1_790_208_000_000  # 2026-09-24T00:00Z ≈ 23-sep 19:00 Bogotá


def test_decision_run_costs_about_95_dollars() -> None:
    assert estimate_run_usd(["A1", "B", "C"], reps=3, turns=400) == pytest.approx(95, abs=5)


def test_quick_run_costs_about_20_dollars() -> None:
    assert estimate_run_usd(["A1", "C"], reps=1, turns=400) == pytest.approx(20, abs=2)


def test_estimate_scales_with_turns_and_arms() -> None:
    one = estimate_run_usd(["A1"], reps=1, turns=100)

    assert estimate_run_usd(["A1"], reps=1, turns=200) == pytest.approx(2 * one)
    assert estimate_run_usd(["A1", "B"], reps=3, turns=100) > 6 * one * 0.99


def _progress(store, run_id: str, started_ms: int, spent: float) -> None:
    store.put_bytes(f"runs/{run_id}/progress.json", json.dumps({"started_at_ms": started_ms, "spent_usd": spent}).encode())


def test_month_spend_adds_this_month_runs_in_bogota_time(tmp_path) -> None:
    store = FilesystemLabStore(tmp_path)
    _progress(store, "run-a", SEP_23 - 86_400_000, 40.0)
    _progress(store, "run-b", SEP_23 - 2 * 86_400_000, 12.5)
    _progress(store, "run-c", SEP_23 - 40 * 86_400_000, 90.0)  # agosto

    assert month_spent_usd(store, now_ms=SEP_23) == pytest.approx(52.5)


def test_month_spend_ignores_broken_progress_files(tmp_path) -> None:
    store = FilesystemLabStore(tmp_path)
    store.put_bytes("runs/run-x/progress.json", b"no-json")

    assert month_spent_usd(store, now_ms=SEP_23) == 0.0


@pytest.mark.parametrize(
    ("estimate", "spent", "fits", "reason"),
    [
        (95.0, 0.0, True, None),
        (130.0, 0.0, False, "run_cap"),
        (95.0, 250.0, False, "month_cap"),
    ],
)
def test_caps(estimate: float, spent: float, fits: bool, reason: str | None) -> None:
    result = check_caps(estimate, run_cap=120.0, month_cap=300.0, month_spent=spent)

    assert (result.fits, result.reason) == (fits, reason)
    assert result.month_left == pytest.approx(300.0 - spent)
