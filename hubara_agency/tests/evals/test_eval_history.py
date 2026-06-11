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


# --------------------------------------------------------------------------- #
# Registros por episodio + razones (los campos nuevos del record).
# --------------------------------------------------------------------------- #
def test_record_carries_episode_avg_passed_and_failed_reasons(tmp_path):
    import json

    hist = tmp_path / "h"
    history.append_history_record(
        hist, run_date="2026-06-05", session_id="wa_a", suite="online",
        scores=[("greeting", 0.0, False, "no saludó con la marca"),
                ("style", 1.0, True, "ok")],
        episode_id="ep_002", ts="2026-06-05T10:00:00+00:00", is_candidate=True,
    )
    raw = (hist / "2026-06-05.jsonl").read_text(encoding="utf-8").strip()
    rec = json.loads(raw)
    assert rec["episode_id"] == "ep_002"
    assert rec["ts"] == "2026-06-05T10:00:00+00:00"
    assert rec["avg"] == 0.5
    assert rec["passed"] is False
    assert rec["is_candidate"] is True
    assert rec["failed"] == [
        {"metric": "greeting", "score": 0.0, "reason": "no saludó con la marca"}
    ]


def test_record_truncates_long_judge_reasons(tmp_path):
    import json

    hist = tmp_path / "h"
    history.append_history_record(
        hist, run_date="2026-06-05", session_id="wa_a", suite="online",
        scores=[("script", 0.2, False, "x" * 1000)],
    )
    rec = json.loads((hist / "2026-06-05.jsonl").read_text(encoding="utf-8"))
    assert len(rec["failed"][0]["reason"]) == 400


# --------------------------------------------------------------------------- #
# Vista por conversación (read_conversation_evals).
# --------------------------------------------------------------------------- #
def test_conversation_evals_groups_by_episode_and_derives_trend(tmp_path):
    hist = tmp_path / "h"
    # ep_001 de wa_a: dos evals, mejora 0.5 -> 0.8 (trend up)
    history.append_history_record(
        hist, run_date="2026-06-01", session_id="wa_a", suite="online",
        scores=[("script", 0.5, False, "flojo")],
        episode_id="ep_001", ts="2026-06-01T08:00:00+00:00", is_candidate=True,
    )
    history.append_history_record(
        hist, run_date="2026-06-02", session_id="wa_a", suite="online",
        scores=[("script", 0.8, True, "ok")],
        episode_id="ep_001", ts="2026-06-02T08:00:00+00:00",
    )
    # ep_002 de wa_a: una sola eval (single) — episodio distinto, grupo distinto
    history.append_history_record(
        hist, run_date="2026-06-02", session_id="wa_a", suite="online",
        scores=[("script", 0.9, True, "ok")],
        episode_id="ep_002", ts="2026-06-02T09:00:00+00:00",
    )

    out = history.read_conversation_evals(
        hist, dates=["2026-06-01", "2026-06-02"], suite="online", threshold=0.7
    )
    assert out["count"] == 2
    by_key = {(c["session_id"], c["episode_id"]): c for c in out["conversations"]}

    ep1 = by_key[("wa_a", "ep_001")]
    assert ep1["evals_count"] == 2
    assert [e["avg"] for e in ep1["evals"]] == [0.5, 0.8]  # cronológico
    assert ep1["trend"] == "up"
    assert ep1["first_avg"] == 0.5 and ep1["last_avg"] == 0.8
    assert ep1["last_passed"] is True
    # la PRIMERA eval falló con razón; la última no tiene falladas
    assert ep1["evals"][0]["failed"][0]["reason"] == "flojo"
    assert ep1["failed_metrics"] == []  # derivado de la ÚLTIMA eval

    ep2 = by_key[("wa_a", "ep_002")]
    assert ep2["trend"] == "single"


def test_conversation_evals_failing_sorted_first(tmp_path):
    hist = tmp_path / "h"
    history.append_history_record(
        hist, run_date="2026-06-02", session_id="wa_ok", suite="online",
        scores=[("script", 0.9, True, "")], episode_id="ep_001",
        ts="2026-06-02T10:00:00+00:00",
    )
    history.append_history_record(
        hist, run_date="2026-06-01", session_id="wa_mala", suite="online",
        scores=[("script", 0.3, False, "mal")], episode_id="ep_001",
        ts="2026-06-01T10:00:00+00:00",
    )
    out = history.read_conversation_evals(
        hist, dates=["2026-06-01", "2026-06-02"], suite="online"
    )
    # La fallada va primero aunque sea más vieja.
    assert out["conversations"][0]["session_id"] == "wa_mala"
    assert out["conversations"][0]["last_passed"] is False


def test_conversation_evals_tolerates_legacy_records(tmp_path):
    """Registros pre-episodio (sin episode_id/avg/failed) siguen siendo legibles."""
    import json

    hist = tmp_path / "h"
    hist.mkdir()
    legacy = {
        "date": "2026-06-01", "session_id": "wa_vieja", "suite": "online",
        "metrics": {"script": 0.4, "style": 0.8},
    }
    (hist / "2026-06-01.jsonl").write_text(
        json.dumps(legacy) + "\n", encoding="utf-8"
    )
    out = history.read_conversation_evals(
        hist, dates=["2026-06-01"], suite="online", threshold=0.7
    )
    assert out["count"] == 1
    conv = out["conversations"][0]
    assert conv["episode_id"] == ""          # sesión entera
    assert conv["last_avg"] == 0.6           # derivado del mapa de métricas
    assert conv["last_passed"] is False      # 0.6 < threshold
    assert conv["evals"][0]["failed"] == []  # sin razones persistidas
    assert conv["trend"] == "single"
