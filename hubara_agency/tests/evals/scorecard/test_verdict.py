"""Veredicto del episodio (HU-SC-1): nunca un promedio.

FALLA si cae un check crítico; ALERTA si cae uno mayor; PASA si no. Un check
de juez sin calibrar no puede reprobar solo (se degrada a mayor). `desconocido`
nunca cuenta como `pasa`.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard.model import CheckResult, CheckSpec
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import Trajectory
from src.plugins.chats.agent.sales_eval.scorecard.verdict import compute_scorecard


def _spec(cid: str, level: str, kind: str = "code") -> CheckSpec:
    return CheckSpec(
        id=cid, name=cid, family="confirmacion", level=level, kind=kind,
        applies="siempre", rule="regla", origin=("test",),
    )


SPECS = {
    s.id: s
    for s in (
        _spec("DES-01", "mayor"),
        _spec("VAR-01", "critico"),
        _spec("CON-01", "critico"),
        _spec("EST-01", "menor"),
        _spec("DES-06", "critico", kind="judge"),
    )
}


def _traj(turns: int = 3, fidelity: str = "trace") -> Trajectory:
    from tests.evals.scorecard.dsl import T, traj

    return traj(*[T(i) for i in range(1, turns + 1)], fidelity=fidelity)


def test_one_critical_failure_fails_the_episode_whatever_else_passes() -> None:
    results = [
        CheckResult("DES-01", "pasa"),
        CheckResult("VAR-01", "pasa"),
        CheckResult("CON-01", "falla", turn=3, evidence="formulario sin sí"),
        CheckResult("EST-01", "pasa"),
    ]

    card = compute_scorecard(_traj(), SPECS, results)

    assert card.verdict == "FALLA"
    assert card.counts == {"critico": 1, "mayor": 0, "menor": 0, "pasa": 3, "no_aplica": 0, "desconocido": 0}
    assert card.first_critical == {"turn": 3, "check_id": "CON-01"}


def test_major_failure_without_critical_is_alert_and_first_failure_is_earliest_turn() -> None:
    results = [
        CheckResult("DES-01", "falla", turn=4),
        CheckResult("EST-01", "falla", turn=2),
        CheckResult("VAR-01", "pasa"),
    ]

    card = compute_scorecard(_traj(), SPECS, results)

    assert card.verdict == "ALERTA"
    assert card.first_failure == {"turn": 2, "check_id": "EST-01"}
    assert card.first_critical is None


def test_only_minor_failures_pass_and_unknown_is_not_counted_as_pass() -> None:
    results = [
        CheckResult("EST-01", "falla", turn=1),
        CheckResult("DES-01", "desconocido"),
        CheckResult("VAR-01", "no_aplica"),
        CheckResult("CON-01", "pasa"),
    ]

    card = compute_scorecard(_traj(), SPECS, results)

    assert card.verdict == "PASA"
    assert card.counts["desconocido"] == 1
    # cumplimiento = pasa / (pasa + falla): desconocido y no_aplica fuera
    assert card.compliance == 0.5


def test_uncalibrated_judge_critical_failure_only_alerts() -> None:
    results = [CheckResult("DES-06", "falla", turn=2, source="judge")]

    card = compute_scorecard(_traj(), SPECS, results)
    assert card.verdict == "ALERTA"
    assert card.results[0]["level"] == "mayor"

    calibrated = compute_scorecard(_traj(), SPECS, results, calibrated={"DES-06"})
    assert calibrated.verdict == "FALLA"


def test_episode_without_turns_has_no_data_verdict() -> None:
    card = compute_scorecard(_traj(turns=0, fidelity="empty"), SPECS, [])

    assert card.verdict == "SIN_DATOS"
