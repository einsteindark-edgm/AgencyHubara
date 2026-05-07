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

from src.sales_whatsapp.workflows.sales_session import HubaraSalesSessionWorkflow

FIXTURE = Path(__file__).parent / "fixtures" / "history_sales_session_v2.json"


async def test_sales_session_replay_does_not_diverge() -> None:
    history = WorkflowHistory.from_json("test-sales", FIXTURE.read_text(encoding="utf-8"))
    replayer = Replayer(workflows=[HubaraSalesSessionWorkflow])
    await replayer.replay_workflow(history)
