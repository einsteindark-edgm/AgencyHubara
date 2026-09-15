"""Agregados del scorecard (HU-SC-3): Pareto de fallos, tasa semanal por check
y embudo de etapa final por veredicto."""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard.stats import compute_stats, week_start


def _row(date: str, verdict: str, stage: str | None, **checks) -> dict:
    return {"session_id": f"wa_{date}_{verdict}", "episode_id": "ep_1", "date": date, "verdict": verdict,
            "stage_final": stage, "checks": dict(checks)}


ROWS = [
    _row("2026-09-01", "FALLA", "confirmacion", **{"CON-01": "falla", "DES-01": "falla", "EST-01": "pasa"}),
    _row("2026-09-02", "ALERTA", "variantes", **{"DES-01": "falla", "CON-01": "no_aplica"}),
    _row("2026-09-09", "PASA", "cierre", **{"DES-01": "pasa", "CON-01": "pasa", "EST-01": "desconocido"}),
]


def test_week_start_is_monday() -> None:
    assert week_start("2026-09-14") == "2026-09-14"  # lunes
    assert week_start("2026-09-17") == "2026-09-14"


def test_pareto_orders_by_failures_with_name_and_level() -> None:
    stats = compute_stats(ROWS, weeks=["2026-08-31", "2026-09-07"])

    assert [(p["check_id"], p["failures"]) for p in stats["pareto"]] == [("DES-01", 2), ("CON-01", 1)]
    assert stats["pareto"][1]["level"] == "critico"
    assert stats["pareto"][0]["name"]
    assert stats["episodes"] == 3
    assert stats["verdicts"] == {"FALLA": 1, "ALERTA": 1, "PASA": 1, "SIN_DATOS": 0}


def test_trend_counts_only_decided_verdicts_per_week() -> None:
    stats = compute_stats(ROWS, weeks=["2026-08-31", "2026-09-07"])

    trend = {t["check_id"]: t for t in stats["trend"]}
    assert trend["DES-01"]["weeks"] == [
        {"week": "2026-08-31", "applicable": 2, "passed": 0, "rate": 0.0},
        {"week": "2026-09-07", "applicable": 1, "passed": 1, "rate": 1.0},
    ]
    assert trend["CON-01"]["weeks"][0] == {"week": "2026-08-31", "applicable": 1, "passed": 0, "rate": 0.0}
    assert trend["EST-01"]["weeks"][1] == {"week": "2026-09-07", "applicable": 0, "passed": 0, "rate": None}


def test_funnel_groups_final_stage_by_verdict_in_funnel_order() -> None:
    stats = compute_stats(ROWS, weeks=["2026-08-31", "2026-09-07"])

    funnel = {f["stage"]: f for f in stats["funnel"]}
    assert [f["stage"] for f in stats["funnel"]][:3] == ["variantes", "confirmacion", "cierre"]
    assert funnel["confirmacion"]["FALLA"] == 1
    assert funnel["cierre"]["PASA"] == 1
