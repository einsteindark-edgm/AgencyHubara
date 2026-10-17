"""`InboundMessageWorkflow`: la entrada durable de UN mensaje del cliente
(ADR 2026-10-15, `docs/adr/2026-10-15-durable-inbound-entry.md`).

El webhook lo arranca con id ``inbound-<wamid>`` y ``REJECT_DUPLICATE`` ANTES de
responderle 200 a Meta: desde ese momento el mensaje vive en Temporal y un
deploy o un OOM ya no lo pierden. Una re-entrega de Meta choca con el id y no
entra dos veces.

Fase 2 (este camino, ``inbound-steps-v1``): cada paso del ingest es una
activity, y lo que un paso ya hizo queda en el historial del workflow (un
reintento de la entrega no vuelve a leer con Jev ni a escribir el historial):

1. se anuncia al ``InboundSessionWorkflow`` de su cliente, que le responde con
   su mensaje anterior (ADR 2026-10-16);
2. el estado de la sesión con sus lecturas y el turno (carrito y cupón) corren
   EN SEGUIDA, en paralelo con los demás mensajes del cliente;
3. una foto o una nota de voz se leen en su propio paso y reentran como texto
   (`<wamid>_vision` / `<wamid>_transcribed`) por los mismos pasos; al final de
   la foto se le avisa a la sesión de ventas que ya no hay nada que esperar;
4. antes de escribir el historial espera, con tope, a que termine el workflow
   de su anterior (el del mismo POST, ``after``, y el que dijo el workflow del
   cliente): la entrega va en orden de llegada sin serializar las lecturas;
5. el historial y la entrega a la sesión.

Fase 1 (runs que estaban vivos al desplegar): espera al anterior del mismo POST
y corre el ingest entero en una activity. Se retira cuando ya no quede ninguno
(un run vive minutos).

DEHA: workflow = driving adapter, toda I/O vía activities (R-DET / R-HEARTBEAT).
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from src.plugins.chats.agent.sales.activities.inbound_entry import (
        PREDECESSOR_GATE_S,
        PREDECESSOR_WAIT_S,
        deliver_inbound_turn_activity,
        describe_inbound_image_activity,
        ingest_inbound_message_activity,
        notify_inbound_photo_done_activity,
        prepare_inbound_turn_activity,
        read_inbound_state_activity,
        register_inbound_arrival_activity,
        transcribe_inbound_audio_activity,
        wait_inbound_predecessor_activity,
        wait_inbound_workflow_activity,
        write_inbound_history_activity,
    )
    from src.plugins.chats.agent.sales.contracts import (
        TURN_DELIVER,
        TURN_DESCRIBE,
        TURN_HISTORY_ONLY,
        TURN_TRANSCRIBE,
        InboundArrival,
        InboundMessageInput,
        IngestRequest,
        inbound_workflow_id,
    )

#: Errores deterministas del código (el tipo de la excepción que lanza la activity).
_PROGRAMMING_ERRORS = (
    "TypeError",
    "KeyError",
    "AttributeError",
    "NameError",
    "UnboundLocalError",
    "IndexError",
    "AssertionError",
    "NotImplementedError",
)

#: El ingest tarda de 1 a 10 s (Jev espera hasta 10 s, una foto anterior hasta
#: 10 s más); 5 min es el techo de un intento. Un worker que muere a mitad se
#: nota por el heartbeat (la activity late cada 10 s) y el intento se repite
#: cuando vuelve un worker. Ante un error, 5 intentos repartidos en ~3 min
#: (5 s, 15 s, 45 s, 2 min): acotado, pero un vault o un Temporal que tarda un
#: par de minutos en volver no deja el mensaje en Failed — Meta ya tiene su 200
#: y no lo re-entrega. Fase 2: la misma política para cada paso.
_PATIENT = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=3.0,
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=5,
    # Un error de programación no se arregla reintentando, y cada intento
    # repetiría Jev, el cupón en Medusa, el reloj del watchdog y analytics
    # (premortem PM-011): falla al primer intento, a la vista en Temporal.
    non_retryable_error_types=list(_PROGRAMMING_ERRORS),
)
_INGEST = {
    "start_to_close_timeout": timedelta(minutes=5),
    "heartbeat_timeout": timedelta(seconds=30),
    "retry_policy": _PATIENT,
}

_WAIT = {
    "start_to_close_timeout": timedelta(seconds=PREDECESSOR_WAIT_S + 30),
    "heartbeat_timeout": timedelta(seconds=30),
    "retry_policy": RetryPolicy(maximum_attempts=3),
}

#: Fase 2. Un paso del ingest: Jev (hasta 10 s), Medusa (topes de 3–5 s), el
#: vault. 2 min es el techo de un intento.
_STEP = {
    "start_to_close_timeout": timedelta(minutes=2),
    "heartbeat_timeout": timedelta(seconds=30),
    "retry_policy": _PATIENT,
}
#: La foto y la nota de voz: el proveedor tarda segundos (una nota de voz de
#: 60 s, algo más). Cada intento cuesta: 3 como mucho.
_MEDIA = {
    "start_to_close_timeout": timedelta(minutes=3),
    "heartbeat_timeout": timedelta(seconds=30),
    "retry_policy": RetryPolicy(
        initial_interval=timedelta(seconds=5),
        backoff_coefficient=3.0,
        maximum_attempts=3,
        non_retryable_error_types=list(_PROGRAMMING_ERRORS),
    ),
}
#: Anuncio y aviso: una señal a Temporal.
_SIGNAL = {
    "start_to_close_timeout": timedelta(seconds=30),
    "retry_policy": RetryPolicy(maximum_attempts=3),
}
_GATE = {
    "start_to_close_timeout": timedelta(seconds=PREDECESSOR_GATE_S + 30),
    "heartbeat_timeout": timedelta(seconds=30),
    "retry_policy": RetryPolicy(maximum_attempts=3),
}
#: Cuánto espera la respuesta del workflow del cliente (quién es su anterior).
#: Llega en milisegundos; si no llega, el mensaje sigue sin el orden entre POSTs.
_ORDER_REPLY_MAX = timedelta(seconds=30)

_STEPS_PATCH = "inbound-steps-v1"


@workflow.defn(name="InboundMessageWorkflow")
class InboundMessageWorkflow:
    def __init__(self) -> None:
        # Los anteriores que dijo el workflow del cliente: el que respondió al
        # anunciarse y los que se anunciaron tarde pero llegaron antes.
        self._answered = False
        self._predecessors: list[str] = []
        self._registered = False
        self._waited = False
        # Workflows anteriores ya esperados (no se espera dos veces al mismo).
        self._waited_for: set[str] = set()

    @workflow.signal
    def predecessor(self, workflow_id: str = "") -> None:
        """La respuesta del ``InboundSessionWorkflow``: el workflow de un
        mensaje anterior del cliente ("" si no hay). Puede llegar más de una:
        un mensaje que llegó antes al webhook pero se anunció después."""
        self._answered = True
        if workflow_id and workflow_id not in self._predecessors:
            self._predecessors.append(workflow_id)

    @workflow.run
    async def run(self, inp: InboundMessageInput) -> str:
        if not workflow.patched(_STEPS_PATCH):
            return await self._whole_ingest(inp)
        request = IngestRequest(message=inp.message, received_at_ms=inp.received_at_ms)
        await self._register(inp)
        if inp.after:
            # Dentro de un POST, el estado espera al anterior (como en la fase
            # 1): los dos verían el mismo episodio cerrado o la misma campaña
            # sin responder. Entre POSTs las lecturas corren en paralelo.
            await self._wait_for([inbound_workflow_id(inp.after)])
        await self._steps(request, inp)
        return "ingested"

    async def _whole_ingest(self, inp: InboundMessageInput) -> str:
        """Fase 1: los runs vivos al desplegar la fase 2 terminan como empezaron."""
        if inp.after:
            try:
                await workflow.execute_activity(wait_inbound_predecessor_activity, inp.after, **_WAIT)
            except ActivityError:
                workflow.logger.warning("inbound_predecessor_wait_failed", extra={"after": inp.after})
        return await workflow.execute_activity(ingest_inbound_message_activity, inp, **_INGEST)

    async def _register(self, inp: InboundMessageInput) -> None:
        """Se anuncia al workflow de su cliente. Si no puede, sigue: pierde el
        orden entre POSTs, nunca el mensaje."""
        session_id = f"wa_{inp.message.get('from_number') or ''}"
        arrival = InboundArrival(workflow_id=workflow.info().workflow_id, received_at_ms=inp.received_at_ms)
        try:
            await workflow.execute_activity(register_inbound_arrival_activity, args=[session_id, arrival], **_SIGNAL)
        except ActivityError:
            workflow.logger.warning("inbound_register_failed", extra={"session_id": session_id})
            return
        self._registered = True

    async def _steps(self, request: IngestRequest, inp: InboundMessageInput) -> None:
        state = await workflow.execute_activity(read_inbound_state_activity, request, **_STEP)
        turn = await workflow.execute_activity(prepare_inbound_turn_activity, args=[request, state], **_STEP)
        if turn.outcome == TURN_TRANSCRIBE:
            reentry = await workflow.execute_activity(transcribe_inbound_audio_activity, request, **_MEDIA)
            if reentry is not None:
                await self._steps(reentry, inp)
            return
        if turn.outcome == TURN_DESCRIBE:
            # Si la foto no se puede leer, el workflow falla a la vista (como
            # cualquier otro paso), pero antes la sesión deja de esperarla.
            try:
                reentry = await workflow.execute_activity(describe_inbound_image_activity, request, **_MEDIA)
                if reentry is not None:
                    await self._steps(reentry, inp)
            finally:
                await self._photo_done(request)
            return
        if turn.outcome in (TURN_DELIVER, TURN_HISTORY_ONLY):
            await self._wait_previous(inp)
            await workflow.execute_activity(write_inbound_history_activity, args=[request, state, turn], **_STEP)
            await workflow.execute_activity(deliver_inbound_turn_activity, args=[request, state, turn], **_STEP)

    async def _wait_previous(self, inp: InboundMessageInput) -> None:
        """Antes de escribir el historial, espera (con tope) a que terminen los
        mensajes anteriores del cliente: el del mismo POST (``after``) y el que
        dijo su workflow. Una sola vez por mensaje."""
        if self._waited:
            return
        self._waited = True
        after = [inbound_workflow_id(inp.after)] if inp.after else []
        if self._registered:
            try:
                await workflow.wait_condition(lambda: self._answered, timeout=_ORDER_REPLY_MAX)
            except asyncio.TimeoutError:
                workflow.logger.warning("inbound_order_reply_missing")
        # Mientras espera, puede enterarse de otro anterior (anunciado tarde).
        while pending := [w for w in [*after, *self._predecessors] if w not in self._waited_for]:
            await self._wait_for(pending)

    async def _wait_for(self, workflow_ids: list[str]) -> None:
        """Espera (con tope) a que terminen esos workflows de entrada, cada uno
        una sola vez. Si la espera falla, sigue: el orden no vale un mensaje."""
        for workflow_id in dict.fromkeys(workflow_ids):
            if workflow_id in self._waited_for:
                continue
            self._waited_for.add(workflow_id)
            try:
                await workflow.execute_activity(wait_inbound_workflow_activity, workflow_id, **_GATE)
            except ActivityError:
                workflow.logger.warning("inbound_previous_wait_failed", extra={"previous": workflow_id})

    async def _photo_done(self, request: IngestRequest) -> None:
        try:
            await workflow.execute_activity(notify_inbound_photo_done_activity, request, **_SIGNAL)
        except ActivityError:
            workflow.logger.warning("inbound_photo_done_notice_failed")
