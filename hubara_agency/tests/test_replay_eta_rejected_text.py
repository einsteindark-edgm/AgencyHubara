"""Replay del gate `eta-rejected-text-as-template-v1` (premortem de la Ola 0 de
Temporal, 2026-10-16).

History SINTÉTICA del ETA con la forma de ANTES: un aviso de `shipping` dentro
de la ventana, WhatsApp no acepta el texto (la activity devuelve `[]`) y el
aviso se persiste y se graba como enviado. El código nuevo lo manda por la
plantilla: comando nuevo en un workflow que vive días (L-9). Generada con el
código actual y el gate forzado a «no» (procedencia en
`fixtures/generate_eta_rejected_text_prepatch_fixture.py`). CONGELADA — no se
regenera; se borra junto con
`workflow.deprecate_patch("eta-rejected-text-as-template-v1")`.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from temporalio import workflow
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from src.plugins.eta.agent.eta.workflows.eta_session import _REJECTED_TEXT_PATCH, HubaraEtaSessionWorkflow

FIXTURE = Path(__file__).parent / "fixtures" / "history_eta_rejected_text_prepatch_v1.json"


def _history() -> WorkflowHistory:
    return WorkflowHistory.from_json("test-eta-rejected-text", FIXTURE.read_text(encoding="utf-8"))


async def test_an_eta_run_from_before_still_replays_its_rejected_text() -> None:
    await Replayer(workflows=[HubaraEtaSessionWorkflow]).replay_workflow(_history())


async def test_the_rejected_text_history_breaks_without_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control negativo: sin su `workflow.patched`, el código nuevo agenda la
    plantilla donde la history persistió el texto."""
    real_patched = workflow.patched
    consulted: list[str] = []

    def ungated(patch_id: str) -> bool:
        if patch_id != _REJECTED_TEXT_PATCH:
            return real_patched(patch_id)
        consulted.append(patch_id)
        return True

    monkeypatch.setattr(workflow, "patched", ungated)
    with pytest.raises(workflow.NondeterminismError):
        await Replayer(workflows=[HubaraEtaSessionWorkflow]).replay_workflow(_history())
    assert consulted == [_REJECTED_TEXT_PATCH]
