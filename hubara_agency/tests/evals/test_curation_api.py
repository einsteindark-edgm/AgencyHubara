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
