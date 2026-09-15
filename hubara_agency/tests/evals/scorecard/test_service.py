"""Servicio del scorecard (HU-SC-1): carga la trayectoria del episodio desde el
vault (trazas si existen, reconstrucción legada si no) y arma el registro."""
from __future__ import annotations

import json
from pathlib import Path

from src.plugins.chats.agent.sales_eval.scorecard import service
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext, CheckResult
from src.plugins.chats.shared import turn_traces

SESSION = "wa_570000000001"


def _write(vault: Path, rel: str, content: str) -> None:
    path = vault / SESSION / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _metadata(vault: Path) -> None:
    _write(vault, "metadata.json", json.dumps({
        "episodes": [
            {"episode_id": "ep_001", "msgs_count_at_start": 0, "msgs_count_at_close": 2, "closing_tag": "RECHAZO"},
            {"episode_id": "ep_002", "msgs_count_at_start": 2, "closing_tag": "INTERESADO"},
        ]
    }))


def test_load_trajectory_prefers_turn_traces(tmp_path: Path) -> None:
    _metadata(tmp_path)
    turn_traces.append_trace(tmp_path, SESSION, {"episode_id": "ep_002", "turn": 1, "trigger": "customer",
                                                 "sent_texts": ["¿Para ti o para regalo?"], "tools": []})
    turn_traces.append_trace(tmp_path, SESSION, {"episode_id": "ep_001", "turn": 1, "trigger": "customer"})

    traj = service.load_trajectory(tmp_path, SESSION, "ep_002")

    assert traj.fidelity == "trace"
    assert [t.sent_texts for t in traj.turns] == [("¿Para ti o para regalo?",)]
    assert traj.closing_tag == "INTERESADO"


def test_load_trajectory_falls_back_to_legacy_dashboard_events(tmp_path: Path) -> None:
    _metadata(tmp_path)
    events = [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "¡Buenos días! Bienvenido a *Hubara*"},
        {"role": "user", "content": "quiero ver velas"},
        {"role": "assistant", "kind": "ui_component", "component_kind": "products_list", "content": "catálogo"},
    ]
    _write(tmp_path, f"sessions/{SESSION}.jsonl", "\n".join(json.dumps(e) for e in events) + "\n")

    traj = service.load_trajectory(tmp_path, SESSION, "ep_002")

    assert traj.fidelity == "legacy"
    assert [t.inbound_text for t in traj.turns] == ["quiero ver velas"]
    assert traj.turns[0].intents == ("products_list",)


def test_score_trajectory_builds_record_with_verdict_and_judge_flag(tmp_path: Path) -> None:
    from tests.evals.scorecard.incidents import CATALOG_CTX, pr281_before_fix

    record = service.score_trajectory(pr281_before_fix(), CATALOG_CTX)

    assert record["verdict"] == "FALLA"
    assert record["judge"] is False
    assert record["registry_version"] >= 1
    assert any(r["check_id"] == "CON-01" and r["verdict"] == "falla" for r in record["results"])

    with_judge = service.score_trajectory(
        pr281_before_fix(), CheckContext(), judge_results=[CheckResult("DES-04", "falla", turn=3, source="judge")]
    )
    assert with_judge["judge"] is True
    assert any(r["check_id"] == "DES-04" and r["source"] == "judge" for r in with_judge["results"])


def test_episode_that_started_before_traces_is_partial(tmp_path: Path) -> None:
    _metadata(tmp_path)
    events = [
        {"role": "user", "content": "hola", "timestamp": "2026-09-14T14:00:00+00:00"},
        {"role": "assistant", "content": "¡Buenos días!", "timestamp": "2026-09-14T14:00:05+00:00"},
        {"role": "user", "content": "¿cuánto cuesta?", "timestamp": "2026-09-14T14:10:00+00:00"},
        {"role": "user", "content": "gracias", "timestamp": "2026-09-14T15:00:00+00:00"},
    ]
    _write(tmp_path, f"sessions/{SESSION}.jsonl", "\n".join(json.dumps(e) for e in events) + "\n")
    # La traza arranca con el deploy, a las 15:00 (turn_started_ms en epoch ms).
    turn_traces.append_trace(tmp_path, SESSION, {"episode_id": "ep_002", "turn": 1, "trigger": "customer",
                                                 "turn_started_ms": 1_789_398_000_000})

    traj = service.load_trajectory(tmp_path, SESSION, "ep_002")

    assert traj.fidelity == "partial"
