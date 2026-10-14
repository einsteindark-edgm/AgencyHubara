"""Replay de histories grabadas con el código ANTERIOR al caso «se calló tras
el catálogo» (commit 11f4f3c1, `remarketing-catalog-silent-v1`).

En prod hay RemarketingWorkflow de la escalera en vuelo (duermen hasta 24 h)
sin el marker nuevo. Revisión de producción del PR #426 (2026-10-14): ninguna
historia previa quedaba en el repo; estas dos se grabaron con el código de
b906635e (activities fake, sesión ficticia wa_573001234567):
  * `full`: 1er toque → señal `next_touch` → 2º toque → el cliente responde →
    relevo a ventas.
  * `inflight`: cortada después del 1er toque (la que encuentra el deploy).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from src.plugins.chats.agent.remarketing.workflows.remarketing import (
    RemarketingSessionWorkflow,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        "history_remarketing_ladder_pre_catalog_silent_full_v1.json",
        "history_remarketing_ladder_pre_catalog_silent_inflight_v1.json",
    ],
)
async def test_histories_previas_al_caso_del_catalogo_replayean_sin_divergir(name: str) -> None:
    history = WorkflowHistory.from_json(
        "remarketing-wa_573001234567", (FIXTURES / name).read_text(encoding="utf-8")
    )
    result = await Replayer(workflows=[RemarketingSessionWorkflow]).replay_workflow(
        history, raise_on_replay_failure=False
    )
    assert result.replay_failure is None, result.replay_failure
