"""`InboundSessionWorkflow`: quién llegó antes, por cliente (ADR 2026-10-16,
`docs/adr/2026-10-16-inbound-session-workflow.md`).

Un workflow por cliente, `inbound-wa_<número>`, en la cola del worker `sales`.
No guarda mensajes ni los entrega: cada `InboundMessageWorkflow` se anuncia al
arrancar (`arrived`, vía `signal_with_start` desde una activity) y este le
responde por la señal `predecessor` con el workflow de su mensaje anterior: el
último anunciado con una llegada al webhook estrictamente menor ("" si no hay).
El mensaje lee en paralelo con los demás y, antes de escribir el historial,
espera con tope a que ese anterior termine.

«Foto en lectura» es entonces un workflow abierto: el de una foto no termina
hasta que la foto entró al bot, y el texto escrito después la espera. Es estado
de Temporal, no de un proceso: el worker `sales` puede tener varias réplicas.

Termina solo tras `_IDLE` sin anuncios; el siguiente mensaje lo vuelve a
arrancar sin anterior (lo de hace más de eso ya terminó, o está trabado y no se
lo espera).

DEHA: sin I/O (solo señales a otros workflows), determinista (R-DET).
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from src.plugins.chats.agent.sales.contracts import InboundArrival, InboundSessionState

#: Sin anuncios en este tiempo, el workflow del cliente termina.
_IDLE = timedelta(minutes=10)
#: Anuncios que recuerda: de sobra para una ráfaga, chico para el historial.
_KEEP = 50
#: Respuestas por run antes del continue-as-new (cada una son pocos eventos).
_CONTINUE_AS_NEW_AFTER = 500


def predecessor_of(recent: list[InboundArrival], arrival: InboundArrival) -> str | None:
    """El anterior de `arrival`: el último de `recent` que llegó al webhook
    estrictamente antes. Estrictamente: dos mensajes del mismo instante no se
    esperan entre sí (dentro de un POST los ordena `after`), así nunca hay un
    ciclo de esperas."""
    earlier = [
        a for a in recent
        if a.received_at_ms < arrival.received_at_ms and a.workflow_id != arrival.workflow_id
    ]
    if not earlier:
        return None
    return max(earlier, key=lambda a: a.received_at_ms).workflow_id


@workflow.defn(name="InboundSessionWorkflow")
class InboundSessionWorkflow:
    def __init__(self) -> None:
        self._arrivals: list[InboundArrival] = []
        self._recent: list[InboundArrival] = []

    @workflow.signal
    def arrived(self, arrival: InboundArrival) -> None:
        """Un mensaje del cliente se anuncia (lo responde el bucle de `run`)."""
        self._arrivals.append(arrival)

    @workflow.run
    async def run(self, state: InboundSessionState | None = None) -> None:
        if state is not None:
            self._recent = list(state.recent)
        answered = 0
        while True:
            try:
                await workflow.wait_condition(lambda: bool(self._arrivals), timeout=_IDLE)
            except asyncio.TimeoutError:
                if not self._arrivals:
                    return
            while self._arrivals:
                arrival = self._arrivals.pop(0)
                previous = predecessor_of(self._recent, arrival)
                self._recent = [*self._recent, arrival][-_KEEP:]
                await self._answer(arrival.workflow_id, previous or "")
                answered += 1
            if answered >= _CONTINUE_AS_NEW_AFTER or workflow.info().is_continue_as_new_suggested():
                await workflow.wait_condition(workflow.all_handlers_finished)
                if not self._arrivals:
                    workflow.continue_as_new(InboundSessionState(recent=self._recent))

    async def _answer(self, workflow_id: str, previous: str) -> None:
        """Le dice al mensaje quién es su anterior. Si ya no está (terminó sin
        esperar la respuesta), no pasa nada."""
        try:
            await workflow.get_external_workflow_handle(workflow_id).signal("predecessor", previous)
        except Exception as exc:  # noqa: BLE001 — un mensaje que ya cerró no necesita respuesta
            workflow.logger.info("inbound_order_answer_skipped", extra={"workflow_id": workflow_id, "error": str(exc)})
