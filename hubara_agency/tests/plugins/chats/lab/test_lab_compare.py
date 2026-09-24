"""Comparación entre bots del laboratorio (plan §5 puntos 4, 6 y 7; PR 13).

Todo sale de los registros del scorecard en modo turno (uno por episodio,
brazo y repetición):

  * fila para las gráficas: la MISMA forma que Calidad LLM, con cada check
    agregado sobre los turnos (falla > pasa > desconocido > sin señal);
  * diferencia pareada contra el control con bootstrap por conversación: si
    el intervalo cruza el cero, "aún no concluyente";
  * pass^k: el episodio pasa en TODAS las repeticiones;
  * fidelidad del simulador: A1 coincide con A0 en ≥ 90 % de los checks de
    código, turno por turno;
  * turnos que cambiaron de veredicto entre dos bots.
"""
from __future__ import annotations

import pytest

from src.plugins.chats.agent.sales_lab.run.compare import (
    FIDELITY_THRESHOLD,
    arm_row,
    changed_turns,
    diff_entry,
    fidelity,
    paired_bootstrap,
    pass_k,
)


def _rec(sid: str, ep: str, verdict: str, turns: dict[int, dict[str, str]], *, stage: str = "descubrimiento") -> dict:
    by_turn = []
    results = []
    for k, checks in sorted(turns.items()):
        rows = [{"check_id": cid, "verdict": v, "turn": k} for cid, v in checks.items()]
        worst = "FALLA" if "falla" in checks.values() else "PASA"
        by_turn.append({"turn": k, "verdict": worst, "results": rows})
        results.extend(rows)
    return {
        "mode": "turn",
        "session_id": sid,
        "episode_id": ep,
        "verdict": verdict,
        "stage_final": stage,
        "episode_date": "2026-09-15",
        "by_turn": by_turn,
        "results": results,
    }


def test_the_row_aggregates_each_check_over_the_turns() -> None:
    rec = _rec("wa_1", "ep_1", "FALLA", {1: {"EST-06": "pasa", "APE-01": "pasa"}, 2: {"EST-06": "falla", "APE-01": "sin_senal"}})

    row = arm_row(rec)

    assert row["checks"] == {"EST-06": "falla", "APE-01": "pasa"}
    assert (row["verdict"], row["stage_final"], row["episode_date"]) == ("FALLA", "descubrimiento", "2026-09-15")
    assert "results" not in row and "by_turn" not in row


def test_paired_bootstrap_is_deterministic_and_says_when_it_is_conclusive() -> None:
    base = {f"wa_{i}": [0.0] for i in range(30)}
    better = {f"wa_{i}": [1.0] for i in range(30)}

    clear = paired_bootstrap(base, better)
    assert clear["delta"] == pytest.approx(1.0) and clear["conclusive"] is True
    assert clear["sessions"] == 30

    noisy_cand = {f"wa_{i}": [1.0 if i % 2 else 0.0] for i in range(30)}
    noisy_base = {f"wa_{i}": [0.0 if i % 2 else 1.0] for i in range(30)}
    noisy = paired_bootstrap(noisy_base, noisy_cand)
    assert noisy["low"] < 0 < noisy["high"] and noisy["conclusive"] is False
    assert paired_bootstrap(noisy_base, noisy_cand) == noisy  # misma semilla, mismo intervalo


def test_bootstrap_only_pairs_conversations_present_in_both() -> None:
    out = paired_bootstrap({"wa_1": [1.0], "wa_2": [0.0]}, {"wa_1": [1.0]})
    assert out["sessions"] == 1
    assert paired_bootstrap({}, {}) == {"delta": None, "low": None, "high": None, "p": None, "conclusive": False, "sessions": 0}


def test_pass_k_needs_every_repetition() -> None:
    turn = {1: {"EST-06": "pasa"}}
    reps = [
        [_rec("wa_1", "ep_1", "PASA", turn), _rec("wa_2", "ep_1", "PASA", turn)],
        [_rec("wa_1", "ep_1", "PASA", turn), _rec("wa_2", "ep_1", "FALLA", turn)],
        [_rec("wa_1", "ep_1", "PASA", turn), _rec("wa_2", "ep_1", "PASA", turn)],
    ]

    assert pass_k(reps) == {"k": 3, "episodes": 2, "rate": 0.5}


def test_fidelity_compares_code_checks_turn_by_turn() -> None:
    a0 = [_rec("wa_1", "ep_1", "PASA", {1: {"EST-06": "pasa", "APE-01": "pasa", "EST-08": "falla"}, 2: {"EST-06": "pasa"}})]
    a1 = [_rec("wa_1", "ep_1", "FALLA", {1: {"EST-06": "pasa", "APE-01": "falla", "EST-08": "pasa"}, 2: {"EST-06": "pasa"}})]

    out = fidelity(a0, [a1], code_checks={"EST-06", "APE-01"})

    # EST-08 es del juez: no cuenta. 3 pares de código, 2 coinciden.
    assert (out["n"], out["agreement"]) == (3, pytest.approx(2 / 3))
    assert out["ok"] is False and out["threshold"] == FIDELITY_THRESHOLD == 0.9


def test_changed_turns_lists_the_turns_whose_verdict_moved() -> None:
    base = [_rec("wa_1", "ep_1", "FALLA", {1: {"EST-06": "pasa"}, 2: {"EST-06": "falla", "APE-03": "pasa"}})]
    cand = [_rec("wa_1", "ep_1", "PASA", {1: {"EST-06": "pasa"}, 2: {"EST-06": "pasa", "APE-03": "pasa"}})]

    assert changed_turns(base, cand) == [
        {"session_id": "wa_1", "episode_id": "ep_1", "turn": 2, "base": "FALLA", "cand": "PASA", "checks": ["EST-06"]}
    ]


def test_diff_entry_puts_it_all_together() -> None:
    base = [[_rec(f"wa_{i}", "ep_1", "FALLA", {1: {"EST-06": "falla"}}) for i in range(20)]]
    cand = [[_rec(f"wa_{i}", "ep_1", "PASA", {1: {"EST-06": "pasa"}}) for i in range(20)]]

    d = diff_entry("A1", "B", base, cand)

    assert (d["base"], d["cand"]) == ("A1", "B")
    assert d["episode_pass"]["delta"] == pytest.approx(1.0) and d["episode_pass"]["conclusive"]
    est06 = next(c for c in d["checks"] if c["check_id"] == "EST-06")
    assert est06["delta"] == pytest.approx(1.0)
    assert d["pass_k"] == {"base": {"k": 1, "episodes": 20, "rate": 0.0}, "cand": {"k": 1, "episodes": 20, "rate": 1.0}}
    assert len(d["changed_turns"]) == 20
