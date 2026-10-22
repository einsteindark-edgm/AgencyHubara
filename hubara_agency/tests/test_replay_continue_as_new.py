"""Replay del gate `continue-as-new-per-run-v1` (auditoría de Temporal, 2026-10-15).

Histories SINTÉTICAS del run que cortó con la regla vieja: arranca con 50 turnos
acumulados, un turno y continue-as-new (el bug: desde el turno 50, cada turno
cortaba y repetía el bootstrap). Generadas con el código de 16c03158, anterior
al cambio (procedencia en `fixtures/generate_continue_as_new_prepatch_fixtures.py`),
una por versión del workflow de ventas y una del ETA (el mismo tope por avisos).
Con la regla nueva el run no corta tras un turno: sin el gate, el replay choca.
CONGELADAS — no se regeneran; se borran junto con
`workflow.deprecate_patch("continue-as-new-per-run-v1")`.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from temporalio import workflow
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from src.platform.temporal.continue_as_new import CONTINUE_AS_NEW_PATCH
from src.plugins.eta.agent.eta.workflows.eta_session import HubaraEtaSessionWorkflow
from tests.sales_workflow_versions import VERSIONS

WORKFLOWS = {**VERSIONS, "eta": HubaraEtaSessionWorkflow}

FIXTURES = Path(__file__).parent / "fixtures"
PREPATCH = {
    "v1": FIXTURES / "history_sales_continue_as_new_loop_prepatch_v1.json",
    "v2": FIXTURES / "history_sales_v2_continue_as_new_loop_prepatch_v1.json",
    "eta": FIXTURES / "history_eta_continue_as_new_loop_prepatch_v1.json",
}


def _history(version: str) -> WorkflowHistory:
    return WorkflowHistory.from_json(f"test-can-loop-{version}", PREPATCH[version].read_text(encoding="utf-8"))


@pytest.mark.parametrize("version", sorted(PREPATCH))
async def test_a_run_that_continued_as_new_by_the_old_rule_still_replays(version: str) -> None:
    await Replayer(workflows=[WORKFLOWS[version]]).replay_workflow(_history(version))


@pytest.mark.parametrize("version", sorted(PREPATCH))
async def test_the_continue_as_new_history_breaks_without_the_gate(
    monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    """Control negativo: la fixture PROTEGE el gate. Con la regla nueva sin su
    `workflow.patched`, el run no corta tras el turno y el replay choca con el
    continue-as-new que la history sí grabó."""
    real_patched = workflow.patched
    consulted: list[str] = []

    def ungated(patch_id: str) -> bool:
        if patch_id != CONTINUE_AS_NEW_PATCH:
            return real_patched(patch_id)
        consulted.append(patch_id)
        return True  # como si la regla nueva no estuviera detrás del gate

    monkeypatch.setattr(workflow, "patched", ungated)

    # El choque exacto: el código agenda la próxima espera (la activity del
    # tiempo de inactividad en ventas, el timer de la semana en el ETA) donde
    # la history grabó el continue-as-new.
    with pytest.raises(workflow.NondeterminismError, match="machine does not handle.*WorkflowExecutionContinuedAsNew"):
        await Replayer(workflows=[WORKFLOWS[version]]).replay_workflow(_history(version))
    assert consulted == [CONTINUE_AS_NEW_PATCH], f"el gate se consultó {len(consulted)} veces"
