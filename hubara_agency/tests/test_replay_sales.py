"""Replay test del workflow de Sales (F6.2 / ADR-005).

Carga la history sintetica capturada por `tests/fixtures/generate_fixtures.py`
y la re-ejecuta contra el codigo actual de `HubaraSalesSessionWorkflow`. Si la
shape de history cambia (orden de activities, signal/query signature, args
serializables) y la fixture queda desactualizada, el `Replayer` lanzara
`NonDeterminismError` y este test fallara.

Cuando se cambie legitimamente la shape, regenerar la fixture con:

    cd hubara_agency
    uv run python tests/fixtures/generate_fixtures.py

Y bumpear el `_v<N>` del filename (ver `tests/fixtures/README.md`).
"""
from __future__ import annotations

from pathlib import Path

from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from src.plugins.chats.agent.sales.workflows.sales_session import HubaraSalesSessionWorkflow

FIXTURE = Path(__file__).parent / "fixtures" / "history_sales_session_v2.json"


async def test_sales_session_replay_does_not_diverge() -> None:
    history = WorkflowHistory.from_json("test-sales", FIXTURE.read_text(encoding="utf-8"))
    replayer = Replayer(workflows=[HubaraSalesSessionWorkflow])
    await replayer.replay_workflow(history)


# History REAL de prod (run 5ed9af2d, 2026-09-18) saneada: sin teléfono, sin API
# keys, sin system prompt ni tool definitions. Tiene la forma PRE
# `escalation-ends-turn-v1`: tras `escalate_to_human` hay un `llm_chat` extra
# (el acuse "Listo, la conversación quedó en manos del equipo humano." que le
# llegó al cliente). El código nuevo corta el turno en la escalación, así que
# este replay es la garantía de que el deploy no rompe runs en vuelo (L-9):
# sin el gate `workflow.patched(...)` falla con NondeterminismError
# ('llm_chat' scheduled vs 'record_turn' command). CONGELADA — no se regenera;
# se borra junto con `workflow.deprecate_patch("escalation-ends-turn-v1")`.
PREPATCH_ESCALATION_FIXTURE = (
    Path(__file__).parent / "fixtures" / "history_sales_escalation_prepatch_v1.json"
)


async def test_prepatch_escalation_history_still_replays() -> None:
    history = WorkflowHistory.from_json(
        "test-sales-escalation-prepatch",
        PREPATCH_ESCALATION_FIXTURE.read_text(encoding="utf-8"),
    )
    replayer = Replayer(workflows=[HubaraSalesSessionWorkflow])
    await replayer.replay_workflow(history)
