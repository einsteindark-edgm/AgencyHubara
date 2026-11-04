"""Replay del gate `scorecard-bands-v1` (Ciclo Colossus F4, 2026-11-03).

History SINTÉTICA del barrido diario con la forma de ANTES: dos episodios
calificados y la eval legada de uno, sin las bandas por regla. El código nuevo
agenda las bandas entre el scorecard y la eval legada: comando nuevo (L-9).
Generada con el código anterior al paso (procedencia en
`fixtures/generate_sales_eval_prebands_fixture.py`). CONGELADA — no se
regenera; se borra junto con `workflow.deprecate_patch("scorecard-bands-v1")`.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from temporalio import workflow
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from src.plugins.chats.agent.sales_eval.workflows.sales_eval import BANDS_PATCH, SalesEvalWorkflow

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "history_sales_eval_prebands_v1.json"


def _history() -> WorkflowHistory:
    return WorkflowHistory.from_json("sales-eval-prebands", FIXTURE.read_text(encoding="utf-8"))


async def test_a_sweep_from_before_the_bands_still_replays() -> None:
    await Replayer(workflows=[SalesEvalWorkflow]).replay_workflow(_history())


async def test_the_prebands_history_breaks_without_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control negativo: sin su `workflow.patched`, el código nuevo agenda las
    bandas donde la history tiene la selección de la eval legada."""
    real_patched = workflow.patched
    consulted: list[str] = []

    def ungated(patch_id: str) -> bool:
        if patch_id != BANDS_PATCH:
            return real_patched(patch_id)
        consulted.append(patch_id)
        return True

    monkeypatch.setattr(workflow, "patched", ungated)
    with pytest.raises(workflow.NondeterminismError):
        await Replayer(workflows=[SalesEvalWorkflow]).replay_workflow(_history())
    assert consulted == [BANDS_PATCH]
