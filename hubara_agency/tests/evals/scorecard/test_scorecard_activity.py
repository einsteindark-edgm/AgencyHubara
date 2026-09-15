"""Activity `score_episode_scorecard` (HU-SC-1): vault → registro guardado +
resumen escalar + alerta en FALLA. Nunca lanza."""
from __future__ import annotations

import json
from pathlib import Path

from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.sales_eval.activities import eval_activities as ea
from src.plugins.chats.agent.sales_eval.evals import composition
from src.plugins.chats.agent.sales_eval.scorecard import alerts, catalog_context, store
from src.plugins.chats.shared import turn_traces
from tests.evals.scorecard.incidents import CATALOG_CTX, pr281_before_fix, traces_from

SESSION = "wa_570000000001"


def _seed(vault: Path) -> None:
    t = pr281_before_fix()
    (vault / SESSION).mkdir(parents=True, exist_ok=True)
    (vault / SESSION / "metadata.json").write_text(json.dumps({
        "episodes": [{"episode_id": "ep_007", "closing_tag": "CONFIRMADO_SIN_DATOS"}],
    }), encoding="utf-8")
    for tr in traces_from(t):
        turn_traces.append_trace(vault, SESSION, tr)


def _patch(monkeypatch, vault: Path, alerts_seen: list) -> None:
    monkeypatch.setattr(composition, "get_vault_dir", lambda: vault)
    monkeypatch.setenv("SCORECARD_JUDGE_ENABLED", "false")

    async def ctx(catalog=None):
        return CATALOG_CTX

    async def notify(record):
        alerts_seen.append(record["verdict"])
        return True

    monkeypatch.setattr(catalog_context, "build_check_context", ctx)
    monkeypatch.setattr(alerts, "notify_failure", notify)


async def test_activity_stores_the_scorecard_and_alerts_on_failure(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path)
    seen: list = []
    _patch(monkeypatch, tmp_path, seen)

    summary = await ActivityEnvironment().run(ea.score_episode_scorecard_activity, SESSION, "ep_007", True)

    assert (summary.verdict, summary.critical, summary.stored, summary.judge) == ("FALLA", 5, True, False)
    assert (summary.first_failure_check, summary.first_failure_turn) == ("DES-09", 2)
    saved = store.find_latest(store.scorecards_dir(tmp_path), SESSION, "ep_007")
    assert saved is not None and saved["verdict"] == "FALLA"
    assert seen == ["FALLA"]


async def test_activity_returns_error_instead_of_raising(tmp_path: Path, monkeypatch) -> None:
    seen: list = []
    _patch(monkeypatch, tmp_path, seen)

    def boom(*_a, **_k):
        raise OSError("disco")

    from src.plugins.chats.agent.sales_eval.scorecard import service

    monkeypatch.setattr(service, "load_trajectory", boom)

    summary = await ActivityEnvironment().run(ea.score_episode_scorecard_activity, SESSION, "ep_007", True)

    assert summary.stored is False
    assert "disco" in summary.error
