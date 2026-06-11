"""Tests del EvaluateEpisodeWorkflow (eval dirigida de UN episodio al cerrar) +
el enumerador del backfill (`select_all_closed_episodes`).

El workflow se corre con el `WorkflowEnvironment` time-skipping de Temporal y la
activity de eval FAKEADA (no toca DeepEval ni el juez LLM): el test verifica el
contrato — que arma el unit_id correcto desde (session_id, episode_id) y delega
en la activity. El backfill se testea como función pura sobre un vault temporal.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.chats.agent.sales_eval.evals import select
from src.plugins.chats.agent.sales_eval.evals.contracts import (
    ConversationEvalResult,
    EvaluateEpisodeInput,
)
from src.plugins.chats.agent.sales_eval.workflows.evaluate_episode import (
    EvaluateEpisodeWorkflow,
)


# --------------------------------------------------------------------------- #
# Workflow: arma el unit_id correcto y delega en la activity.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_workflow_builds_unit_id_and_delegates():
    seen: dict[str, str] = {}

    # Sin type-hint del dataclass: en un closure anidado Temporal no resuelve el
    # hint y pasa `window` como dict (con todos los campos serializados). Leemos
    # la key directo — alcanza para verificar el contrato del workflow.
    @activity.defn(name="evaluate_sales_conversation")
    async def fake_eval(unit_id: str, window: dict) -> ConversationEvalResult:
        seen["unit_id"] = unit_id
        seen["draft_goldens"] = window["draft_goldens"]
        sid, _, eid = unit_id.partition("::")
        return ConversationEvalResult(
            session_id=sid, episode_id=eid, num_turns=8, metrics_evaluated=9,
            metrics_passed=4, avg_score=0.42, overall_pass=False, is_candidate=True,
        )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="eval-ep-test",
            workflows=[EvaluateEpisodeWorkflow],
            activities=[fake_eval],
        ):
            result = await env.client.execute_workflow(
                EvaluateEpisodeWorkflow.run,
                EvaluateEpisodeInput(
                    session_id="wa_+573001112233",
                    episode_id="ep_002",
                    closing_tag="COMPRA_EXITOSA",
                ),
                id="eval-episode-wa_+573001112233-ep_002",
                task_queue="eval-ep-test",
            )

    assert seen["unit_id"] == "wa_+573001112233::ep_002"  # unit_id correcto
    assert seen["draft_goldens"] is True  # los malos se auto-curan a golden
    assert result.episode_id == "ep_002"
    assert result.is_candidate is True


@pytest.mark.asyncio
async def test_workflow_legacy_session_without_episode():
    """episode_id vacío → unit_id pelado (sesión entera, compat legacy)."""
    seen: dict[str, str] = {}

    @activity.defn(name="evaluate_sales_conversation")
    async def fake_eval(unit_id: str, window: dict) -> ConversationEvalResult:
        seen["unit_id"] = unit_id
        return ConversationEvalResult(session_id=unit_id, num_turns=5)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="eval-ep-test2",
            workflows=[EvaluateEpisodeWorkflow],
            activities=[fake_eval],
        ):
            await env.client.execute_workflow(
                EvaluateEpisodeWorkflow.run,
                EvaluateEpisodeInput(session_id="wa_legacy"),
                id="eval-episode-wa_legacy-",
                task_queue="eval-ep-test2",
            )
    assert seen["unit_id"] == "wa_legacy"  # sin "::"


# --------------------------------------------------------------------------- #
# Backfill: enumera episodios cerrados, sin ventana, excluyendo ya evaluados.
# --------------------------------------------------------------------------- #
def _write_session(vault: Path, sid: str, n_msgs: int, episodes: list[dict]) -> None:
    d = vault / sid / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    with (d / f"{sid}.jsonl").open("w", encoding="utf-8") as f:
        for i in range(n_msgs):
            f.write(json.dumps({"role": "user", "content": f"m{i}"}) + "\n")
    (vault / sid / "metadata.json").write_text(
        json.dumps({"episodes": episodes}), encoding="utf-8"
    )


def test_backfill_lists_all_closed_episodes_no_window(tmp_path: Path):
    # 3 episodios: ep_001 (cerrado, viejo), ep_002 (cerrado), ep_003 (ACTIVO).
    _write_session(tmp_path, "wa_a", 12, [
        {"episode_id": "ep_001", "msgs_count_at_start": 0, "msgs_count_at_close": 5,
         "closed_at_ms": 100},
        {"episode_id": "ep_002", "msgs_count_at_start": 5, "msgs_count_at_close": 12,
         "closed_at_ms": 200},
        {"episode_id": "ep_003", "msgs_count_at_start": 12, "msgs_count_at_close": None,
         "closed_at_ms": None},
    ])
    units = select.select_all_closed_episodes(vault_dir=tmp_path, min_turns=4)
    # ep_003 (activo) excluido; ep_001/ep_002 (cerrados) presentes, sin importar antigüedad.
    assert set(units) == {"wa_a::ep_001", "wa_a::ep_002"}


def test_backfill_excludes_already_evaluated(tmp_path: Path):
    _write_session(tmp_path, "wa_a", 12, [
        {"episode_id": "ep_001", "msgs_count_at_start": 0, "msgs_count_at_close": 6,
         "closed_at_ms": 100},
        {"episode_id": "ep_002", "msgs_count_at_start": 6, "msgs_count_at_close": 12,
         "closed_at_ms": 200},
    ])
    units = select.select_all_closed_episodes(
        vault_dir=tmp_path, min_turns=4, already={("wa_a", "ep_001")}
    )
    assert units == ["wa_a::ep_002"]  # ep_001 ya tenía nota → no se re-evalúa


def test_backfill_respects_min_turns_per_episode(tmp_path: Path):
    _write_session(tmp_path, "wa_a", 10, [
        {"episode_id": "ep_001", "msgs_count_at_start": 0, "msgs_count_at_close": 8,
         "closed_at_ms": 100},
        {"episode_id": "ep_002", "msgs_count_at_start": 8, "msgs_count_at_close": 10,
         "closed_at_ms": 200},  # 2 turnos < min_turns
    ])
    units = select.select_all_closed_episodes(vault_dir=tmp_path, min_turns=4)
    assert units == ["wa_a::ep_001"]


def test_backfill_legacy_session_without_episodes(tmp_path: Path):
    d = tmp_path / "wa_legacy" / "sessions"
    d.mkdir(parents=True)
    with (d / "wa_legacy.jsonl").open("w", encoding="utf-8") as f:
        for i in range(6):
            f.write(json.dumps({"role": "user", "content": f"m{i}"}) + "\n")
    units = select.select_all_closed_episodes(vault_dir=tmp_path, min_turns=4)
    assert units == ["wa_legacy"]  # sesión entera


def test_read_evaluated_session_episodes(tmp_path: Path):
    from src.plugins.chats.agent.sales_eval.evals import history

    hist = tmp_path / "h"
    history.append_history_record(
        hist, run_date="2026-06-01", session_id="wa_a", suite="online",
        scores=[("greeting", 0.9, True, "")], episode_id="ep_001",
    )
    history.append_history_record(
        hist, run_date="2026-06-02", session_id="wa_b", suite="online",
        scores=[("greeting", 0.5, False, "x")],  # episode_id vacío (legacy)
    )
    seen = history.read_evaluated_session_episodes(hist)
    assert seen == {("wa_a", "ep_001"), ("wa_b", "")}
