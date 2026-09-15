"""Store de scorecards y etiquetas (HU-SC-1/SC-4) — JSONL por día bajo el vault."""
from __future__ import annotations

from pathlib import Path

from src.plugins.chats.agent.sales_eval.scorecard import store


def _card(session: str, episode: str, verdict: str, ts: str, **checks) -> dict:
    return {
        "session_id": session, "episode_id": episode, "verdict": verdict, "fidelity": "trace",
        "counts": {}, "compliance": 1.0, "first_failure": None, "first_critical": None,
        "stage_final": "cierre", "closing_tag": None, "turns": 3, "judge": False,
        "results": [{"check_id": k, "verdict": v, "level": "mayor", "turn": None, "evidence": "", "critique": "", "source": "code"}
                    for k, v in checks.items()],
        "ts": ts,
    }


def test_append_then_list_keeps_latest_per_episode_ordered_by_verdict(tmp_path: Path) -> None:
    d = tmp_path / "scorecards"
    store.append_scorecard(d, _card("wa_1", "ep_1", "PASA", "2026-09-10T10:00:00+00:00"))
    store.append_scorecard(d, _card("wa_1", "ep_1", "FALLA", "2026-09-11T10:00:00+00:00", **{"CON-01": "falla"}))
    store.append_scorecard(d, _card("wa_2", "ep_1", "ALERTA", "2026-09-12T10:00:00+00:00"))
    store.append_scorecard(d, _card("wa_3", "ep_2", "PASA", "2026-09-13T10:00:00+00:00"))

    rows = store.list_scorecards(d, dates=["2026-09-10", "2026-09-11", "2026-09-12", "2026-09-13"])

    assert [(r["session_id"], r["verdict"]) for r in rows] == [
        ("wa_1", "FALLA"), ("wa_2", "ALERTA"), ("wa_3", "PASA"),
    ]
    assert rows[0]["checks"] == {"CON-01": "falla"}
    assert "results" not in rows[0]
    assert rows[0]["date"] == "2026-09-11"


def test_find_latest_returns_full_record_with_results(tmp_path: Path) -> None:
    d = tmp_path / "scorecards"
    store.append_scorecard(d, _card("wa_1", "ep_1", "PASA", "2026-09-10T10:00:00+00:00"))
    store.append_scorecard(d, _card("wa_1", "ep_1", "FALLA", "2026-09-11T10:00:00+00:00", **{"CON-01": "falla"}))

    found = store.find_latest(d, "wa_1", "ep_1")

    assert found is not None and found["verdict"] == "FALLA"
    assert found["results"][0]["check_id"] == "CON-01"
    assert store.find_latest(d, "wa_9", "ep_1") is None


def test_labels_append_and_filter(tmp_path: Path) -> None:
    path = tmp_path / "labels" / "labels.jsonl"
    store.append_label(path, {"session_id": "wa_1", "episode_id": "ep_1", "check_id": "DES-04", "verdict": "falla", "note": ""})
    store.append_label(path, {"session_id": "wa_2", "episode_id": "ep_1", "check_id": "DES-04", "verdict": "pasa", "note": ""})
    store.append_label(path, {"session_id": "wa_1", "episode_id": "ep_1", "check_id": "DES-04", "verdict": "pasa", "note": "corrijo"})

    assert len(store.read_labels(path)) == 3
    latest = store.latest_labels(store.read_labels(path))
    assert latest[("wa_1", "ep_1", "DES-04")]["verdict"] == "pasa"
    assert [lab["session_id"] for lab in store.read_labels(path, session_id="wa_2", episode_id="ep_1")] == ["wa_2"]
