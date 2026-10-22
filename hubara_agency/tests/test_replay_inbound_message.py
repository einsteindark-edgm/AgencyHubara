"""Replay de la entrada durable (ADR 2026-10-15 y 2026-10-16).

Mensajes en vuelo durante un deploy reproducen su history contra el código
nuevo: si un cambio agrega, quita o reordena activities o señales sin
`workflow.patched`, el `Replayer` lanza `NonDeterminismError` y este test falla.

* `history_inbound_message_chained_v1.json`: un run de la fase 1, ENCADENADO
  (espera al mensaje anterior del mismo cliente → ingest entero). Lo siguen
  reproduciendo los runs que estaban vivos al desplegar la fase 2.
* `history_inbound_message_steps_v2.json`: la fase 2 (`inbound-steps-v1`), una
  foto encadenada a un texto: anuncio al workflow del cliente, su respuesta,
  estado, turno, visión, reentrada, espera al anterior, historial, entrega y
  aviso de la foto.
* `history_inbound_session_v1.json`: el `InboundSessionWorkflow` de ese cliente
  (dos anuncios, dos respuestas, esperando el próximo).

Si el cambio es legítimo (detrás de su patch), generar una fixture nueva con el
código viejo y bumpear el `_v<N>`.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from src.plugins.chats.agent.sales.workflows.inbound_message import InboundMessageWorkflow
from src.plugins.chats.agent.sales.workflows.inbound_session import InboundSessionWorkflow

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("fixture", "workflow_id"),
    [
        ("history_inbound_message_chained_v1.json", "inbound-wamid.FIXTURE.B"),
        ("history_inbound_message_steps_v2.json", "inbound-wamid.FIXTURE.B"),
        ("history_inbound_session_v1.json", "inbound-wa_573001234567"),
    ],
)
async def test_inbound_replay_does_not_diverge(fixture: str, workflow_id: str) -> None:
    history = WorkflowHistory.from_json(workflow_id, (FIXTURES / fixture).read_text(encoding="utf-8"))
    await Replayer(workflows=[InboundMessageWorkflow, InboundSessionWorkflow]).replay_workflow(history)
