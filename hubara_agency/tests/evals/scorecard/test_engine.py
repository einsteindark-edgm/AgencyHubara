"""Motor de checks de código (HU-SC-1)."""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard import engine
from src.plugins.chats.agent.sales_eval.scorecard.checks import CODE_CHECKS
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from tests.evals.scorecard.dsl import T, traj


def test_empty_episode_is_not_applicable_everywhere() -> None:
    results = engine.run_code_checks(traj(fidelity="empty"), CheckContext())

    assert results
    assert {r.verdict for r in results} == {"no_aplica"}


def test_a_check_that_raises_is_unknown_not_silently_dropped(monkeypatch) -> None:
    def boom(_traj, _ctx):
        raise RuntimeError("roto")

    monkeypatch.setitem(CODE_CHECKS, "EST-06", boom)

    results = {r.check_id: r for r in engine.run_code_checks(traj(T(1)), CheckContext())}

    assert results["EST-06"].verdict == "desconocido"
    assert "roto" in results["EST-06"].evidence
