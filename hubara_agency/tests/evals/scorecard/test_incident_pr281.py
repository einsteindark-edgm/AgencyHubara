"""Aceptación (HU-SC-1): el episodio del PR #281 bajo el scorecard.

El evaluador legado le dio 0.93. Con checks de código solamente (sin juez),
el scorecard debe reprobarlo con los críticos anclados a sus turnos, y el
mismo episodio con las guardas nuevas debe quedar en ALERTA: el cliente se
salvó, pero el LLM siguió intentando lo mismo.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard.engine import run_code_checks
from src.plugins.chats.agent.sales_eval.scorecard.registry import SPECS_BY_ID
from src.plugins.chats.agent.sales_eval.scorecard.verdict import compute_scorecard
from tests.evals.scorecard.incidents import CATALOG_CTX, pr281_after_fix, pr281_before_fix


def _failures(card) -> dict[str, int | None]:
    return {r["check_id"]: r["turn"] for r in card.results if r["verdict"] == "falla"}


def test_pr281_before_fix_fails_with_critical_checks_on_their_turns() -> None:
    t = pr281_before_fix()

    card = compute_scorecard(t, SPECS_BY_ID, run_code_checks(t, CATALOG_CTX))

    assert card.verdict == "FALLA"
    failures = _failures(card)
    assert failures == {
        "DES-01": 4,
        "DES-09": 2,
        "VAR-01": 5,
        "CON-01": 9,
        "CON-02": 9,
        "ENV-02": 9,
        "EST-06": 9,
        "TAG-01": 10,
        "TAG-02": 10,
        "GHO-02": 10,
    }
    assert card.counts["critico"] == 5
    assert card.first_failure == {"turn": 2, "check_id": "DES-09"}
    assert card.first_critical == {"turn": 5, "check_id": "VAR-01"}
    assert card.counts["desconocido"] == 0


def test_pr281_after_fix_is_alert_guards_saved_the_customer() -> None:
    t = pr281_after_fix()

    card = compute_scorecard(t, SPECS_BY_ID, run_code_checks(t, CATALOG_CTX))

    assert card.verdict == "ALERTA"
    failures = _failures(card)
    assert {"VAR-01b", "CON-05", "TAG-01b"} <= set(failures)
    assert not {"VAR-01", "CON-01", "CON-02", "TAG-01", "TAG-02"} & set(failures)
    assert card.counts["critico"] == 0
