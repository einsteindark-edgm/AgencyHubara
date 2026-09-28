"""Interruptor del juez LLM del eval (`EVAL_LLM_JUDGE_ENABLED`) — APAGADO por defecto.

Decisión del operador 2026-09-28: evaluar con IA costaba demasiado. El juez
(`gemini-pro-judge` = Gemini 3.1 Pro, razonamiento "high" por defecto) hacía
~18 llamadas de scorecard + ~28 de métricas DeepEval por CADA episodio cerrado,
más el barrido diario: ~USD 1 por episodio, unas 200 veces lo que cuesta atender
la conversación con DeepSeek. Se apaga hasta definir otra estrategia.

Contrato: sin la variable, NINGÚN camino del eval llama al juez (scorecard al
cierre y diario, recálculo del dashboard, métricas legadas, borrador de golden,
suite de goldens). Los checks de CÓDIGO del scorecard siguen corriendo: son
gratis. Con `EVAL_LLM_JUDGE_ENABLED=true` todo vuelve a ser como antes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.sales_eval.activities import eval_activities as ea
from src.plugins.chats.agent.sales_eval.evals import composition, reconstruct, scenario
from src.plugins.chats.agent.sales_eval.evals.contracts import EvalWindowInput, GoldenEvalInput
from src.plugins.chats.agent.sales_eval.scorecard import alerts, catalog_context, judge_checks
from src.plugins.chats.api import evals as evals_api
from src.plugins.chats.api import scorecards as scorecards_api
from src.plugins.chats.shared import turn_traces
from tests.evals.scorecard.incidents import CATALOG_CTX, pr281_before_fix, traces_from

SESSION = "wa_100000000001"


@pytest.fixture(autouse=True)
def _no_switch_in_env(monkeypatch) -> None:
    # El .env local puede traerla: el contrato es el DEFAULT del código.
    monkeypatch.delenv("EVAL_LLM_JUDGE_ENABLED", raising=False)


@pytest.fixture
def judge_calls(monkeypatch) -> list[str]:
    calls: list[str] = []

    def get_judge():
        calls.append("get_judge")
        raise RuntimeError("se llamó al juez LLM")

    async def run_judge_checks(traj, ctx, judge, **_kw):
        calls.append("run_judge_checks")
        return []

    monkeypatch.setattr(composition, "get_judge", get_judge)
    monkeypatch.setattr(judge_checks, "run_judge_checks", run_judge_checks)
    return calls


def _seed_episode(vault: Path) -> None:
    (vault / SESSION).mkdir(parents=True, exist_ok=True)
    (vault / SESSION / "metadata.json").write_text(json.dumps({
        "episodes": [{"episode_id": "ep_007", "closing_tag": "CONFIRMADO_SIN_DATOS"}],
    }), encoding="utf-8")
    for tr in traces_from(pr281_before_fix()):
        turn_traces.append_trace(vault, SESSION, tr)


@pytest.fixture
def scorecard_env(tmp_path: Path, monkeypatch) -> Path:
    _seed_episode(tmp_path)
    monkeypatch.setattr(composition, "get_vault_dir", lambda: tmp_path)

    async def ctx(catalog=None):
        return CATALOG_CTX

    async def notify(record):
        return True

    monkeypatch.setattr(catalog_context, "build_check_context", ctx)
    monkeypatch.setattr(alerts, "notify_failure", notify)
    return tmp_path


async def test_scorecard_runs_only_code_checks_when_the_switch_is_unset(scorecard_env, judge_calls) -> None:
    summary = await ActivityEnvironment().run(ea.score_episode_scorecard_activity, SESSION, "ep_007", True)

    assert judge_calls == []
    assert (summary.stored, summary.judge, summary.verdict) == (True, False, "FALLA")


async def test_scorecard_calls_the_judge_again_when_the_switch_is_on(
    scorecard_env, judge_calls, monkeypatch
) -> None:
    monkeypatch.setenv("EVAL_LLM_JUDGE_ENABLED", "true")
    monkeypatch.setattr(composition, "get_judge", lambda: object())

    await ActivityEnvironment().run(ea.score_episode_scorecard_activity, SESSION, "ep_007", True)

    assert judge_calls == ["run_judge_checks"]


async def test_legacy_eval_is_skipped_without_calling_the_judge(tmp_path: Path, monkeypatch, judge_calls) -> None:
    # Un episodio con turnos suficientes: hoy llegaría hasta el juez.
    monkeypatch.setattr(composition, "get_vault_dir", lambda: tmp_path)
    monkeypatch.setattr(reconstruct, "read_episode_events", lambda *_a, **_k: ([], {"episode_id": "ep_007"}))
    monkeypatch.setattr(reconstruct, "to_evaluable_turns", lambda *_a, **_k: [object()] * 6)
    monkeypatch.setattr(reconstruct, "read_session_metadata", lambda *_a, **_k: {})
    monkeypatch.setattr(reconstruct, "build_conversational_test_case", lambda *_a, **_k: object())
    monkeypatch.setattr(scenario, "build_scenario", lambda *_a, **_k: "")

    async def no_catalog():
        return ""

    monkeypatch.setattr(scenario, "catalog_ground_truth", no_catalog)

    result = await ActivityEnvironment().run(
        ea.evaluate_sales_conversation_activity, f"{SESSION}::ep_007", EvalWindowInput()
    )

    assert judge_calls == []
    assert (result.skipped, result.error, result.metrics_evaluated) == (True, "llm_judge_disabled", 0)


async def test_golden_suite_runs_without_the_judge_when_the_switch_is_unset(monkeypatch) -> None:
    cmds: list[list[str]] = []

    class _Proc:
        returncode = 0

        def __init__(self) -> None:
            self.stdout = self._lines()

        async def _lines(self):
            for _ in ():
                yield b""

        async def wait(self) -> int:
            return 0

    async def fake_exec(*cmd, **_kw):
        cmds.append(list(cmd))
        Path(cmd[list(cmd).index("--json") + 1]).write_text("[]", encoding="utf-8")
        return _Proc()

    monkeypatch.setattr(ea.asyncio, "create_subprocess_exec", fake_exec)

    result = await ActivityEnvironment().run(ea.run_golden_suite_activity, GoldenEvalInput())

    assert "--no-judge" in cmds[0]
    assert result.judge is False


def test_dashboard_rescore_with_judge_does_not_queue_it_when_the_switch_is_unset(
    scorecard_env, monkeypatch
) -> None:
    started: list = []

    async def fake_start(session_id: str, episode_id: str) -> str:
        started.append((session_id, episode_id))
        return "scorecard-wf-1"

    monkeypatch.setattr(scorecards_api, "get_vault_dir", lambda: scorecard_env)
    monkeypatch.setattr(scorecards_api, "_start_judge_workflow", fake_start)
    app = FastAPI()
    app.include_router(evals_api.router, prefix="/api/chats")

    detail = TestClient(app).post(
        "/api/chats/evals/scorecard/rescore",
        json={"session_id": SESSION, "episode_id": "ep_007", "judge": True},
    ).json()

    assert started == []
    assert detail["stored"] is True
    assert detail["judge_queued"] is False
    assert "apagado" in detail["judge_error"]
