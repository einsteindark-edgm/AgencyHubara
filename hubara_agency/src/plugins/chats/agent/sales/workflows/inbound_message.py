"""`InboundMessageWorkflow`: la entrada durable de UN mensaje del cliente
(ADR 2026-10-15, `docs/adr/2026-10-15-durable-inbound-entry.md`).

El webhook lo arranca con id ``inbound-<wamid>`` y ``REJECT_DUPLICATE`` ANTES de
responderle 200 a Meta: desde ese momento el mensaje vive en Temporal y un
deploy o un OOM ya no lo pierden. Una re-entrega de Meta choca con el id y no
entra dos veces.

1. Si el webhook lo encadenó al mensaje anterior del mismo cliente en el mismo
   POST (``after``), espera a que el workflow de ese mensaje cierre (el orden
   de hoy). Si la espera no puede (no existe, falló, se pasó del tope), sigue:
   ningún mensaje se pierde por esperar el orden.
2. Corre el ingest de siempre (``IngestInboundMessage.execute``) en una activity
   con heartbeat y reintentos acotados. Lo que un reintento no puede repetir
   (historial, aviso a la sesión, envíos al cliente) lo frena el diario de
   efectos del ingest.

DEHA: workflow = driving adapter, toda I/O vía activities (R-DET / R-HEARTBEAT).
"""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from src.plugins.chats.agent.sales.activities.inbound_entry import (
        PREDECESSOR_WAIT_S,
        ingest_inbound_message_activity,
        wait_inbound_predecessor_activity,
    )
    from src.plugins.chats.agent.sales.contracts import InboundMessageInput

#: El ingest tarda de 1 a 10 s (Jev espera hasta 10 s, una foto anterior hasta
#: 10 s más); 5 min es el techo de un intento. Un worker que muere a mitad se
#: nota por el heartbeat (la activity late cada 10 s) y el intento se repite.
_INGEST = {
    "start_to_close_timeout": timedelta(minutes=5),
    "heartbeat_timeout": timedelta(seconds=30),
    "retry_policy": RetryPolicy(
        initial_interval=timedelta(seconds=2),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(seconds=30),
        maximum_attempts=3,
    ),
}

_WAIT = {
    "start_to_close_timeout": timedelta(seconds=PREDECESSOR_WAIT_S + 30),
    "heartbeat_timeout": timedelta(seconds=30),
    "retry_policy": RetryPolicy(maximum_attempts=3),
}


@workflow.defn(name="InboundMessageWorkflow")
class InboundMessageWorkflow:
    @workflow.run
    async def run(self, inp: InboundMessageInput) -> str:
        if inp.after:
            try:
                await workflow.execute_activity(wait_inbound_predecessor_activity, inp.after, **_WAIT)
            except ActivityError:
                workflow.logger.warning("inbound_predecessor_wait_failed", extra={"after": inp.after})
        return await workflow.execute_activity(ingest_inbound_message_activity, inp, **_INGEST)
