"""Replay de `InboundMessageWorkflow` (entrada durable, ADR 2026-10-15).

La fixture es un run ENCADENADO (espera al mensaje anterior del mismo cliente →
ingest), así cubre los dos comandos del workflow. Mensajes en vuelo durante un
deploy reproducen su history contra el código nuevo: si un cambio agrega,
quita o reordena activities sin `workflow.patched`, el `Replayer` lanza
`NonDeterminismError` y este test falla.

Si el cambio es legítimo (detrás de su patch), generar una fixture nueva con el
código viejo y bumpear el `_v<N>`.
"""
from __future__ import annotations

from pathlib import Path

from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from src.plugins.chats.agent.sales.workflows.inbound_message import InboundMessageWorkflow

FIXTURE = Path(__file__).parent / "fixtures" / "history_inbound_message_chained_v1.json"


async def test_inbound_message_replay_does_not_diverge() -> None:
    history = WorkflowHistory.from_json("inbound-wamid.FIXTURE.B", FIXTURE.read_text(encoding="utf-8"))
    await Replayer(workflows=[InboundMessageWorkflow]).replay_workflow(history)
