"""Servicio del scorecard en modo turno (plan del laboratorio §5.2–5.5).

El laboratorio pasa la trayectoria REAL del episodio y un turno simulado por
caso; recibe el resultado por turno y el veredicto del brazo simulado con la
misma regla de `verdict.py`.
"""
from __future__ import annotations

from dataclasses import replace

from src.plugins.chats.agent.sales_eval.scorecard.model import CheckResult
from src.plugins.chats.agent.sales_eval.scorecard.registry import REGISTRY_VERSION
from src.plugins.chats.agent.sales_eval.scorecard.service import aggregate_checks, score_turns
from tests.evals.scorecard.dsl import T, tool
from tests.evals.scorecard.incidents import CATALOG_CTX, pr281_before_fix


def _real_candidates(real, *, skip_ghost: bool = True):
    return {t.turn: t for t in real.turns if not (skip_ghost and t.is_ghost)}


def test_real_turns_as_candidates_fail_the_simulated_episode_like_production() -> None:
    real = pr281_before_fix()

    out = score_turns(real, _real_candidates(real), CATALOG_CTX, episodes_at={})

    assert out["mode"] == "turn"
    assert out["registry_version"] == REGISTRY_VERSION
    assert [b["turn"] for b in out["by_turn"]] == [1, 2, 3, 4, 5, 6, 7, 9]
    assert out["verdict"] == "FALLA"
    by_turn = {b["turn"]: b for b in out["by_turn"]}
    assert by_turn[2]["verdict"] == "PASA" and by_turn[2]["first_failure"] == {"turn": 2, "check_id": "DES-09"}
    assert by_turn[5]["verdict"] == "FALLA"
    assert by_turn[1]["verdict"] == "PASA" and by_turn[1]["first_failure"] is None
    assert all(r["turn"] == b["turn"] for b in out["by_turn"] for r in b["results"])
    assert len(out["results"]) == sum(len(b["results"]) for b in out["by_turn"])
    assert all("turn" in r and "level" in r for r in out["results"])


def test_a_clean_candidate_replaces_the_real_turn_and_its_failures() -> None:
    real = pr281_before_fix()
    clean = replace(real.turns[4], sent_texts=(), llm_text="", tools=(tool("present_variant_picker"),),
                    intents=("variant_picker",))

    out = score_turns(real, {5: clean}, CATALOG_CTX)

    rows = {r["check_id"]: r for r in out["results"]}
    assert rows["VAR-01"]["verdict"] == "no_aplica"
    assert out["by_turn"][0]["turn"] == 5
    assert out["verdict"] != "FALLA"


def test_judge_results_are_merged_per_turn_and_pinned_to_it() -> None:
    real = pr281_before_fix()
    judge = {
        2: [CheckResult("EST-08", "falla", turn=2, evidence="ignoró la pregunta", source="judge")],
        3: [CheckResult("EST-08", "falla", turn=1, evidence="otro turno", source="judge")],
    }

    out = score_turns(real, {2: real.turns[1], 3: real.turns[2]}, CATALOG_CTX, judge_results=judge)

    by_turn = {b["turn"]: {r["check_id"]: r for r in b["results"]} for b in out["by_turn"]}
    assert by_turn[2]["EST-08"]["verdict"] == "falla"
    assert by_turn[3]["EST-08"]["verdict"] == "desconocido"
    assert by_turn[3]["EST-08"]["turn"] == 3


def test_future_checks_show_no_signal_in_discovery_turns() -> None:
    real = pr281_before_fix()

    out = score_turns(real, {2: real.turns[1]}, CATALOG_CTX)

    b = out["by_turn"][0]
    rows = {r["check_id"]: r for r in b["results"]}
    assert rows["CIE-04"]["verdict"] == "sin_senal"
    assert rows["GHO-02"]["verdict"] == "sin_senal"
    assert b["counts"]["sin_senal"] >= 2


def test_no_candidates_is_no_data() -> None:
    out = score_turns(pr281_before_fix(), {}, CATALOG_CTX)

    assert out["by_turn"] == [] and out["results"] == []
    assert out["verdict"] == "SIN_DATOS"


def test_aggregate_checks_keeps_the_strongest_signal_per_check() -> None:
    rows = [
        {"check_id": "A", "verdict": "pasa", "turn": 1},
        {"check_id": "A", "verdict": "falla", "turn": 2},
        {"check_id": "A", "verdict": "no_aplica", "turn": 3},
        {"check_id": "B", "verdict": "sin_senal", "turn": 1},
        {"check_id": "B", "verdict": "desconocido", "turn": 2},
        {"check_id": "C", "verdict": "no_aplica", "turn": 1},
        {"check_id": "C", "verdict": "sin_senal", "turn": 2},
        {"check_id": "D", "verdict": "pasa", "turn": 1},
        {"check_id": "D", "verdict": "desconocido", "turn": 2},
    ]

    assert aggregate_checks(rows) == {"A": "falla", "B": "desconocido", "C": "sin_senal", "D": "pasa"}


def test_candidate_with_a_new_turn_number_is_appended_after_the_prefix() -> None:
    real = pr281_before_fix()
    extra = T(11, inbound="¿sigues ahí?", sent=["¡Hola! Sí, aquí estoy"], stage_in="confirmacion")

    out = score_turns(real, {11: extra}, CATALOG_CTX)

    assert [b["turn"] for b in out["by_turn"]] == [11]
