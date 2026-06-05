"""Tests del histórico de scores (tendencia) — append + agregación por métrica/día."""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.evals import history


def _scores(**kv: float):
    # (metric_key, score, success, reason)
    return [(k, v, v >= 0.7, "") for k, v in kv.items()]


def test_append_and_trend_aggregation(tmp_path):
    hist = tmp_path / "history"
    # día 1: 2 conversaciones online
    history.append_history_record(
        hist, run_date="2026-06-01", session_id="wa_a", suite="online",
        scores=_scores(script_adherence=0.4, no_hallucination=1.0),
    )
    history.append_history_record(
        hist, run_date="2026-06-01", session_id="wa_b", suite="online",
        scores=_scores(script_adherence=0.8, no_hallucination=0.6),
    )
    # día 2: 1 conversación, script mejora
    history.append_history_record(
        hist, run_date="2026-06-02", session_id="wa_c", suite="online",
        scores=_scores(script_adherence=0.9, no_hallucination=1.0),
    )
    # un golden el día 2 -> NO debe mezclarse con la suite online
    history.append_history_record(
        hist, run_date="2026-06-02", session_id="wa_golden_x", suite="golden",
        scores=_scores(script_adherence=0.2),
    )

    trend = history.read_trend(
        hist, dates=["2026-06-01", "2026-06-02"], suite="online", threshold=0.7
    )
    assert trend["suite"] == "online"
    assert set(trend["metrics"]) == {"script_adherence", "no_hallucination"}

    by_metric = {s["metric"]: s["points"] for s in trend["series"]}
    sa = {p["date"]: p for p in by_metric["script_adherence"]}
    # día 1: avg(0.4, 0.8)=0.6, min 0.4, n 2, 1 por debajo de 0.7
    assert sa["2026-06-01"] == {"date": "2026-06-01", "avg": 0.6, "min": 0.4, "n": 2, "n_below": 1}
    # día 2: solo la online (0.9), el golden 0.2 NO entra
    assert sa["2026-06-02"] == {"date": "2026-06-02", "avg": 0.9, "min": 0.9, "n": 1, "n_below": 0}


def test_trend_empty_when_no_files(tmp_path):
    trend = history.read_trend(tmp_path / "nope", dates=["2026-06-01"], suite="online")
    assert trend["series"] == []
    assert trend["metrics"] == []


def test_suite_filter_golden(tmp_path):
    hist = tmp_path / "h"
    history.append_history_record(
        hist, run_date="2026-06-03", session_id="g1", suite="golden",
        scores=_scores(role_adherence=0.5),
    )
    trend = history.read_trend(hist, dates=["2026-06-03"], suite="golden")
    assert trend["metrics"] == ["role_adherence"]
    assert trend["series"][0]["points"][0]["n_below"] == 1  # 0.5 < 0.7
