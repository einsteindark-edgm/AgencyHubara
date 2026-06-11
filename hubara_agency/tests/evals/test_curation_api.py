"""Tests del API de curación de goldens (`src/plugins/chats/api/evals.py`).

No requieren deepeval ni juez — corren en la suite default. Monkeypatchean el dir
de candidatos a un tmp_path.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from src.plugins.chats.api import evals as api


def _candidate(name: str, avg_scores: list[tuple[str, float, bool]]) -> dict:
    return {
        "scenario": "esc",
        "expected_outcome": "debió saludar",
        "turns": [{"role": "user", "content": "hola"}, {"role": "assistant", "content": "Buenas tardes. Hubara."}],
        "additional_metadata": {
            "source": "auto_curation",
            "status": "needs_human_review",
            "failed_metrics": [{"metric": k, "score": s} for k, s, ok in avg_scores if not ok],
            "all_scores": [{"metric": k, "score": s, "success": ok} for k, s, ok in avg_scores],
        },
    }


@pytest.fixture
def candidates_dir(tmp_path: Path, monkeypatch) -> Path:
    d = tmp_path / "_candidates"
    d.mkdir()
    monkeypatch.setattr(api, "get_candidates_dir", lambda: d)
    return d


def _write(d: Path, name: str, data: dict) -> None:
    (d / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_list_candidates(candidates_dir: Path):
    _write(candidates_dir, "wa_1", _candidate("wa_1", [("greeting", 0.0, False), ("style", 1.0, True)]))
    _write(candidates_dir, "wa_2", _candidate("wa_2", [("script", 0.5, False)]))
    out = api.list_candidates()
    assert out["count"] == 2
    ids = {c["id"] for c in out["candidates"]}
    assert ids == {"wa_1", "wa_2"}
    c1 = next(c for c in out["candidates"] if c["id"] == "wa_1")
    assert c1["avg_score"] == 0.5  # (0.0 + 1.0) / 2
    assert "greeting" in c1["failed_metrics"]


def test_get_candidate(candidates_dir: Path):
    _write(candidates_dir, "wa_1", _candidate("wa_1", [("greeting", 0.0, False)]))
    full = api.get_candidate("wa_1")
    assert full["expected_outcome"] == "debió saludar"
    assert len(full["turns"]) == 2


def test_get_missing_candidate_404(candidates_dir: Path):
    with pytest.raises(HTTPException) as exc:
        api.get_candidate("inexistente")
    assert exc.value.status_code == 404


def test_path_traversal_rejected(candidates_dir: Path):
    with pytest.raises(HTTPException) as exc:
        api.get_candidate("../../../etc/passwd")
    assert exc.value.status_code == 400


def test_approve_moves_to_approved_and_edits(candidates_dir: Path):
    _write(candidates_dir, "wa_1", _candidate("wa_1", [("greeting", 0.0, False)]))
    res = api.approve_candidate("wa_1", {"expected_outcome": "saludo por hora + marca"})
    assert res["status"] == "approved"
    assert not (candidates_dir / "wa_1.json").exists()  # movido fuera de pendientes
    approved = candidates_dir / "approved" / "wa_1.json"
    assert approved.exists()
    data = json.loads(approved.read_text(encoding="utf-8"))
    assert data["expected_outcome"] == "saludo por hora + marca"
    assert data["additional_metadata"]["status"] == "human_reviewed"


def test_discard_deletes(candidates_dir: Path):
    _write(candidates_dir, "wa_1", _candidate("wa_1", [("greeting", 0.0, False)]))
    res = api.discard_candidate("wa_1")
    assert res["status"] == "discarded"
    assert not (candidates_dir / "wa_1.json").exists()


# --------------------------------------------------------------------------- #
# /evals/conversations — vista por episodio con cross-ref de candidatos.
# --------------------------------------------------------------------------- #
def _candidate_for(session_id: str, episode_id: str) -> dict:
    data = _candidate(session_id, [("greeting", 0.0, False)])
    data["additional_metadata"]["source_session_redacted"] = session_id
    data["additional_metadata"]["source_episode"] = episode_id
    return data


@pytest.fixture
def eval_dirs(tmp_path: Path, monkeypatch):
    """Candidates + history + vault apuntando a tmp (los 3 stores del API)."""
    cand = tmp_path / "_candidates"
    cand.mkdir()
    hist = tmp_path / "_history"
    vault = tmp_path / "vault"
    monkeypatch.setattr(api, "get_candidates_dir", lambda: cand)
    monkeypatch.setattr(api, "get_eval_history_dir", lambda: hist)
    monkeypatch.setattr(api, "get_vault_dir", lambda: vault)
    return cand, hist, vault


def test_conversations_endpoint_joins_history_candidates_and_metadata(eval_dirs):
    from datetime import datetime, timezone

    from src.plugins.chats.agent.sales_eval.evals import history as hist_mod

    cand, hist, vault = eval_dirs
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Histórico: un episodio que falló y quedó candidato.
    hist_mod.append_history_record(
        hist, run_date=today, session_id="wa_1", suite="online",
        scores=[("greeting", 0.0, False, "no saludó")],
        episode_id="ep_001", ts=f"{today}T10:00:00+00:00", is_candidate=True,
    )
    # Candidato pendiente para ese episodio.
    _write(cand, "wa_1__ep_001", _candidate_for("wa_1", "ep_001"))
    # Metadata con el closing_tag del episodio.
    (vault / "wa_1").mkdir(parents=True)
    (vault / "wa_1" / "metadata.json").write_text(
        json.dumps({"episodes": [
            {"episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": 2,
             "closing_tag": "RECHAZO", "order_id": None},
        ]}),
        encoding="utf-8",
    )

    out = api.eval_conversations(days=7, suite="online")
    assert out["count"] == 1
    conv = out["conversations"][0]
    assert (conv["session_id"], conv["episode_id"]) == ("wa_1", "ep_001")
    assert conv["candidate_id"] == "wa_1__ep_001"
    assert conv["candidate_status"] == "pending"
    assert conv["closing_tag"] == "RECHAZO"
    assert conv["evals"][0]["failed"][0]["reason"] == "no saludó"


def test_conversations_endpoint_marks_approved_candidates(eval_dirs):
    from datetime import datetime, timezone

    from src.plugins.chats.agent.sales_eval.evals import history as hist_mod

    cand, hist, _vault = eval_dirs
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    hist_mod.append_history_record(
        hist, run_date=today, session_id="wa_2", suite="online",
        scores=[("style", 0.5, False, "voseo")],
        episode_id="ep_003", ts=f"{today}T11:00:00+00:00", is_candidate=True,
    )
    approved = cand / "approved"
    approved.mkdir()
    _write(approved, "wa_2__ep_003", _candidate_for("wa_2", "ep_003"))

    out = api.eval_conversations(days=7, suite="online")
    conv = out["conversations"][0]
    assert conv["candidate_status"] == "approved"


# --------------------------------------------------------------------------- #
# /evals/transcript — el segmento evaluado del episodio.
# --------------------------------------------------------------------------- #
def _write_session(vault: Path, session_id: str, events: list[dict]) -> None:
    d = vault / session_id / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    with (d / f"{session_id}.jsonl").open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def test_transcript_slices_episode_and_redacts(eval_dirs):
    _cand, _hist, vault = eval_dirs
    _write_session(vault, "wa_3", [
        {"role": "user", "content": "episodio viejo"},
        {"role": "assistant", "content": "respuesta vieja"},
        {"role": "user", "content": "hola, mi cel es 3001112233"},
        {"role": "assistant", "content": "Buenas tardes. Bienvenido a Hubara.",
         "tool_calls": [{"name": "send_quick_replies"}]},
    ])
    (vault / "wa_3" / "metadata.json").write_text(
        json.dumps({"episodes": [
            {"episode_id": "ep_001", "msgs_count_at_start": 0, "msgs_count_at_close": 2},
            {"episode_id": "ep_002", "msgs_count_at_start": 2, "msgs_count_at_close": None},
        ]}),
        encoding="utf-8",
    )

    out = api.eval_transcript(session_id="wa_3", episode_id="ep_002")
    assert out["episode_id"] == "ep_002"
    assert len(out["turns"]) == 2                      # solo el slice del episodio
    assert "<PHONE>" in out["turns"][0]["content"]     # PII redactada
    assert out["turns"][1]["tools"] == ["send_quick_replies"]
    assert out["truncated_at_human_takeover"] is False


def test_transcript_rejects_bad_ids(eval_dirs):
    with pytest.raises(HTTPException) as exc:
        api.eval_transcript(session_id="../../etc/passwd", episode_id="")
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        api.eval_transcript(session_id="wa_3", episode_id="../x")
    assert exc.value.status_code == 400


def test_transcript_404_when_session_missing(eval_dirs):
    with pytest.raises(HTTPException) as exc:
        api.eval_transcript(session_id="wa_inexistente", episode_id="")
    assert exc.value.status_code == 404


def test_summary_exposes_session_and_episode(candidates_dir: Path):
    _write(candidates_dir, "wa_9__ep_004", _candidate_for("wa_9", "ep_004"))
    out = api.list_candidates()
    c = out["candidates"][0]
    assert c["session_id"] == "wa_9"
    assert c["episode_id"] == "ep_004"
