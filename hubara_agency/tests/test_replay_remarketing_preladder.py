"""Replay de histories grabadas con el código ANTERIOR a la escalera.

En prod hay RemarketingWorkflow en vuelo (duermen hasta 24h esperando al
cliente) grabados sin el marker `remarketing-ladder-v1` pero CON todos los
markers previos (eligibility, policy-gate, context-v1, debounce, …). El cambio
de la escalera reordenó el arranque y tocó el loop: estas fixtures prueban que
esas histories siguen replayeando sin NondeterminismError (hallazgo M-3 de la
revisión: `history_remarketing_session_v3.json` es prehistórica, sin markers).

Generadas con el código de `main` previo al cambio (activities fake, sesión
ficticia wa_573001234567):
  * `preladder_next_touch`: gancho → llega un SIGNAL `next_touch` (el deploy
    nuevo del ciclo señalando un workflow viejo) → el cliente responde → handoff.
  * `preladder_abstention`: `NO_MESSAGE` con `record_turn` (el path viejo SÍ
    grababa la abstención — el nuevo no, y no debe divergir en replay).
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
        "history_remarketing_preladder_next_touch_v1.json",
        "history_remarketing_preladder_abstention_v1.json",
    ],
)
async def test_histories_previas_a_la_escalera_replayean_sin_divergir(name: str) -> None:
    history = WorkflowHistory.from_json(
        "remarketing-wa_573001234567", (FIXTURES / name).read_text(encoding="utf-8")
    )
    result = await Replayer(workflows=[RemarketingSessionWorkflow]).replay_workflow(
        history, raise_on_replay_failure=False
    )
    assert result.replay_failure is None, result.replay_failure
