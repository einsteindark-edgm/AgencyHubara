"""CampaignSendWorkflowV2 — una campaña grande, por lotes y bajo control del
operador (R-DET: cero I/O acá).

Por qué existe (auditoría de Temporal, 2026-10-15): el V1 recorría a todos
los destinatarios en una sola historia (≈12 eventos por persona) y llegaba al
límite de 51.200 eventos cerca del destinatario 4.000, con un tope de 5.000
importados; el plan entero viajaba como un payload (cerca de los 2 MB); no se
podía pausar ni cancelar a mitad de envío, y un error de stamp dejaba la
campaña en «sending» para siempre.

- El plan se guarda en el vault (`prepare_campaign_send`): acá solo viajan el
  conteo y un cursor.
- Cada lote de `batch_size` destinatarios es un workflow hijo
  (`CampaignSendBatchWorkflow`) con su propia historia; el lote vuelve a
  mirar bajas, casos en manos de un humano y horario de silencio al enviar.
- El padre hace continue-as-new cada `batches_per_run` lotes con el cursor y
  los contadores.
- Updates `pause` / `resume` / `cancel` (con validadores) actúan entre lotes:
  el lote en vuelo termina. Query `progress` y el vault muestran el avance.
- Cualquier error deja la campaña cerrada con el motivo (fallida, o
  cancelada si la cancelaron desde Temporal) — nunca «sending» colgada.

Las campañas programadas con el tipo viejo (`CampaignSendWorkflow`) terminan
con el V1, que sigue registrado sin cambios.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, CancelledError, ChildWorkflowError

with workflow.unsafe.imports_passed_through():
    from src.plugins.marketing.agent.campaigns.activities import PLAN_MAX_ATTEMPTS
    from src.plugins.marketing.domain.campaign_send import (
        DELIVERY_OPTED_OUT,
        DELIVERY_SENT,
        DELIVERY_SKIPPED,
        SEND_CONCURRENCY,
        STATE_CANCELLING,
        CampaignBatch,
        CampaignBatchInput,
        CampaignBatchResult,
        CampaignDelivery,
        CampaignDeliveryOutcome,
        CampaignSendInput,
        CampaignSendProgress,
        CampaignSendResume,
        CampaignSendStart,
        advance_progress,
        breaker_reason,
        sends_task_queue,
    )
    from src.plugins.marketing.domain.campaigns import (
        STATUS_CANCELLED,
        STATUS_FAILED,
        STATUS_PAUSED,
        STATUS_SENDING,
        STATUS_SENT,
        RecipientSendPlan,
        SkippedRecipient,
    )
    from src.sdk.messagingkit import CarouselCard

_FAST = timedelta(seconds=30)
#: Arma la audiencia escaneando el vault (en un hilo: el heartbeat late).
_PLAN_TIMEOUT = timedelta(minutes=5)
#: Si el worker muere a mitad (un deploy), Temporal reintenta a los 30 s y no
#: al vencer el start_to_close: el reintento cae dentro de la dedup de 2 min
#: del send de plataforma y la activity mira el touch antes de reenviar.
_HEARTBEAT = timedelta(seconds=30)
#: Envío a Meta + touch. Sin schedule-to-start: con el ritmo del queue de
#: envíos, esperar turno no es un error (la activity re-chequea el horario de
#: silencio al correr).
_SEND_TIMEOUT = timedelta(seconds=60)
_CAROUSEL_TIMEOUT = timedelta(seconds=60)
#: Retries acotados por destinatario: un número inválido no debe colgar la
#: campaña (los non-retryable de Meta cortan solos en el 1er intento).
_SEND_RETRY = RetryPolicy(maximum_attempts=3)
#: Una pausa sin reanudar se cancela sola pasado este tiempo (con el motivo):
#: nadie la olvida «enviándose» para siempre.
PAUSE_LIMIT = timedelta(days=7)

#: Escrituras al vault: idempotentes (el stamp por campaña+contacto, el lote
#: por su inicio), así que reintentar es seguro.
_VAULT_RETRY = RetryPolicy(maximum_attempts=5)



@workflow.defn(name="CampaignSendBatchWorkflow")
class CampaignSendBatchWorkflow:
    """Un lote: carga sus destinatarios (re-chequeados ahora) y entrega cada
    mensaje con UNA activity (envío + touch). Su historia queda acotada por
    el tamaño del lote."""

    @workflow.run
    async def run(self, inp: CampaignBatchInput) -> CampaignBatchResult:
        batch = await workflow.execute_activity(
            "load_campaign_batch",
            args=[inp.campaign_id, inp.start, inp.end],
            result_type=CampaignBatch,
            start_to_close_timeout=_FAST,
            retry_policy=_VAULT_RETRY,
        )
        if batch.quiet_until_ms is not None:
            # La mayoría está en su horario de silencio: no sale nada y el
            # padre espera a que abra la ventana (no se queman como omitidos).
            return CampaignBatchResult(
                start=inp.start, end=inp.end, deferred_until_ms=batch.quiet_until_ms
            )
        sends_queue = sends_task_queue(workflow.info().task_queue)
        outcomes: list[CampaignDeliveryOutcome | ActivityError] = []
        for i in range(0, len(batch.recipients), SEND_CONCURRENCY):
            chunk = batch.recipients[i : i + SEND_CONCURRENCY]
            outcomes.extend(
                await asyncio.gather(
                    *(self._deliver(inp, batch, recipient, sends_queue) for recipient in chunk)
                )
            )
        return _batch_result(inp, batch, outcomes)

    async def _deliver(
        self,
        inp: CampaignBatchInput,
        batch: CampaignBatch,
        recipient: RecipientSendPlan,
        sends_queue: str,
    ) -> CampaignDeliveryOutcome | ActivityError:
        try:
            return await workflow.execute_activity(
                "deliver_campaign_message",
                CampaignDelivery(
                    campaign_id=inp.campaign_id,
                    session_id=recipient.session_id,
                    template_name=inp.template_name,
                    variables=recipient.variables,
                    touch=batch.touch,
                    cards=inp.cards,
                    bypass_cooldown=recipient.bypass_cooldown,
                ),
                result_type=CampaignDeliveryOutcome,
                task_queue=sends_queue,
                start_to_close_timeout=_SEND_TIMEOUT,
                heartbeat_timeout=_HEARTBEAT,
                retry_policy=_SEND_RETRY,
            )
        except ActivityError as e:
            # Un número inválido, una plantilla pausada…: cuenta como fallido
            # con su tipo y la campaña sigue (el padre decide si frenar).
            return e


def _batch_result(
    inp: CampaignBatchInput,
    batch: CampaignBatch,
    outcomes: list[CampaignDeliveryOutcome | ActivityError],
) -> CampaignBatchResult:
    sent = 0
    failed: list[str] = []
    opted_out: list[str] = []
    skipped = list(batch.skipped)
    unstamped: list[str] = []
    failure_types: dict[str, int] = {}
    failure_example: str | None = None
    for recipient, outcome in zip(batch.recipients, outcomes, strict=True):
        session_id = recipient.session_id
        if isinstance(outcome, ActivityError):
            failed.append(session_id)
            kind, message = _error_kind(outcome)
            failure_types[kind] = failure_types.get(kind, 0) + 1
            failure_example = failure_example or message
        elif outcome.outcome == DELIVERY_SENT:
            sent += 1
            if not outcome.stamped:
                unstamped.append(session_id)
        elif outcome.outcome == DELIVERY_OPTED_OUT:
            opted_out.append(session_id)
        else:
            skipped.append(SkippedRecipient(session_id, outcome.skip_reason or DELIVERY_SKIPPED))
    return CampaignBatchResult(
        start=inp.start,
        end=inp.end,
        sent=sent,
        failed=failed,
        opted_out=opted_out,
        skipped=skipped,
        unstamped=unstamped,
        failure_types=failure_types,
        failure_example=failure_example,
    )


def _error_kind(error: ActivityError) -> tuple[str, str]:
    """(tipo, mensaje) del error de más adentro: `TemplateMetaError132012`."""
    cause: BaseException = error
    while getattr(cause, "cause", None) is not None:
        cause = cause.cause  # type: ignore[attr-defined]
    kind = getattr(cause, "type", None) or type(cause).__name__
    message = getattr(cause, "message", None) or str(cause)
    return kind, message


@workflow.defn(name="CampaignSendWorkflowV2")
class CampaignSendWorkflowV2:
    @workflow.init
    def __init__(self, inp: CampaignSendInput) -> None:
        self._campaign_id = inp.campaign_id
        self._progress = CampaignSendProgress(total=0)
        self._paused = False
        #: Por qué se pausó sola (None = la pausó el operador).
        self._pause_reason: str | None = None
        #: Pausada por horario de silencio: cuándo sigue sola (epoch ms).
        self._resume_at_ms: int | None = None
        self._cancel_requested = False
        #: Motivo del cierre cuando no es un error (p. ej. pausada 7 días).
        self._close_reason: str | None = None
        #: Ya no se aceptan Updates: el envío se está cerrando.
        self._done = False
        #: El envío arrancó (la campaña ya se marcó enviándose): recién desde
        #: acá una cancelación la cierra. Antes (programada que todavía no se
        #: disparó) la API la devuelve a borrador.
        self._started = False
        #: La campaña ya quedó cerrada en el vault: nada más la toca.
        self._closed = False
        self._recorded_state: str | None = None
        #: Una escritura al vault a la vez: los Updates escriben su estado al
        #: momento y el loop escribe cada lote; la última lleva lo vigente.
        self._vault = asyncio.Lock()

    @workflow.run
    async def run(self, inp: CampaignSendInput) -> dict:
        try:
            summary = await self._send(inp)
        except (asyncio.CancelledError, Exception) as exc:
            # Nunca «sending» colgada: cancelado desde Temporal (llega directo,
            # o envuelto en el error del lote hijo que se canceló con él) o
            # cualquier otro error cierra la campaña con el motivo.
            self._done = True
            if _is_cancellation(exc):
                if self._started:
                    await self._finish(STATUS_CANCELLED, "Se canceló el envío desde Temporal.")
            else:
                await self._finish(STATUS_FAILED, _failure_reason(exc, self._progress))
            await workflow.wait_condition(workflow.all_handlers_finished)
            raise
        await workflow.wait_condition(workflow.all_handlers_finished)
        return summary

    async def _send(self, inp: CampaignSendInput) -> dict:
        if inp.resume is None:
            start = await workflow.execute_activity(
                "prepare_campaign_send",
                inp.campaign_id,
                result_type=CampaignSendStart,
                start_to_close_timeout=_PLAN_TIMEOUT,
                heartbeat_timeout=_HEARTBEAT,
                retry_policy=RetryPolicy(maximum_attempts=PLAN_MAX_ATTEMPTS),
            )
            if start.blocked_reason:
                # El cupón ya no servía a la hora del disparo: no sale nada. La
                # activity dejó la campaña fallida con el motivo (no se pisa).
                self._done = True
                return {**_summary(self._progress, "blocked"), "blocked_reason": start.blocked_reason}
            template_name = start.template_name
            # Carrusel: las tarjetas se arman UNA vez y viajan a cada lote.
            cards: list[CarouselCard] = []
            if start.carousel:
                cards = await workflow.execute_activity(
                    "prepare_campaign_carousel",
                    inp.campaign_id,
                    result_type=list[CarouselCard],
                    start_to_close_timeout=_CAROUSEL_TIMEOUT,
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
            self._progress = CampaignSendProgress(total=start.total)
            self._started = True
            workflow.logger.info(
                "campaña %s: arranca el envío a %d destinatarios", inp.campaign_id, start.total
            )
            await self._record(None)
            if start.total == 0:
                self._done = True
                await self._finish(STATUS_FAILED, _NOBODY_REASON)
                return _summary(self._progress, STATUS_FAILED)
        else:
            self._started = True
            template_name = inp.resume.template_name
            cards = inp.resume.cards
            self._progress = inp.resume.progress
            self._paused = inp.resume.progress.paused
            self._pause_reason = inp.resume.progress.pause_reason
            self._resume_at_ms = inp.resume.progress.resume_at_ms
            self._recorded_state = inp.resume.progress.state

        batches_this_run = 0
        while self._progress.processed < self._progress.total:
            if self._paused and not self._cancel_requested:
                await self._wait_while_paused()
            if self._cancel_requested:
                break
            if (
                batches_this_run >= inp.batches_per_run
                or workflow.info().is_continue_as_new_suggested()
            ):
                await workflow.wait_condition(workflow.all_handlers_finished)
                if self._paused or self._cancel_requested:
                    continue
                workflow.continue_as_new(
                    replace(
                        inp,
                        resume=CampaignSendResume(
                            progress=self._current(), template_name=template_name, cards=cards
                        ),
                    )
                )
            begin = self._progress.processed
            end = min(begin + inp.batch_size, self._progress.total)
            try:
                result = await workflow.execute_child_workflow(
                    CampaignSendBatchWorkflow.run,
                    CampaignBatchInput(
                        campaign_id=inp.campaign_id,
                        campaign_name=inp.campaign_name,
                        start=begin,
                        end=end,
                        template_name=template_name,
                        cards=cards,
                    ),
                    id=f"{workflow.info().workflow_id}-batch-{begin:07d}",
                )
            except ChildWorkflowError as e:
                if _is_cancellation(e):
                    raise
                # «Fallida» no tiene vuelta y perdía al resto de la audiencia:
                # el lote no avanzó, se pausa con el motivo y «Reanudar» lo
                # vuelve a intentar.
                self._auto_pause(
                    f"El lote desde el destinatario {begin + 1} falló: {_innermost_message(e)}. "
                    "«Reanudar envío» lo vuelve a intentar."
                )
                continue
            if result.deferred_until_ms is not None:
                self._auto_pause(_QUIET_REASON, resume_at_ms=result.deferred_until_ms)
                continue
            self._progress = advance_progress(self._progress, result)
            batches_this_run += 1
            stop = breaker_reason(result)
            if stop is not None:
                self._auto_pause(stop)
            await self._record(result)

        self._done = True
        outcome = STATUS_CANCELLED if self._cancel_requested else STATUS_SENT
        await self._finish(outcome, self._close_reason)
        return _summary(self._progress, outcome)

    def _auto_pause(self, reason: str, *, resume_at_ms: int | None = None) -> None:
        if self._cancel_requested:
            return
        workflow.logger.warning("campaña %s: se pausa sola — %s", self._campaign_id, reason)
        self._paused = True
        self._pause_reason = reason
        self._resume_at_ms = resume_at_ms

    async def _wait_while_paused(self) -> None:
        await self._record_if_changed()
        if self._resume_at_ms is not None:
            wait = timedelta(milliseconds=self._resume_at_ms - _epoch_ms(workflow.now()))
            timeout = max(wait, timedelta(seconds=1))
        else:
            timeout = PAUSE_LIMIT
        try:
            await workflow.wait_condition(
                lambda: not self._paused or self._cancel_requested, timeout=timeout
            )
        except TimeoutError:
            if self._resume_at_ms is not None:
                # Abrió el horario de los clientes: sigue sola.
                self._paused = False
            else:
                self._cancel_requested = True
                self._close_reason = (
                    f"Estuvo pausada más de {PAUSE_LIMIT.days} días sin reanudarse: "
                    "se canceló sola. Lo que alcanzó a salir, salió."
                )
        if not self._paused:
            self._pause_reason = None
            self._resume_at_ms = None
        if not self._cancel_requested:
            await self._record_if_changed()

    async def _record_if_changed(self) -> None:
        await self._record(None, only_if_changed=True)

    async def _record(
        self, batch: CampaignBatchResult | None, *, only_if_changed: bool = False
    ) -> None:
        async with self._vault:
            if self._closed:
                return
            progress = self._current()
            # Se compara ADENTRO del candado: una pausa y una reanudación
            # seguidas no dejan el vault con el estado viejo.
            if only_if_changed and (not self._started or progress.state == self._recorded_state):
                return
            workflow.set_current_details(_details(progress))
            await workflow.execute_activity(
                "record_campaign_progress",
                args=[self._campaign_id, progress, batch],
                start_to_close_timeout=_FAST,
                retry_policy=_VAULT_RETRY,
            )
            self._recorded_state = progress.state

    async def _finish(self, outcome: str, reason: str | None) -> None:
        async with self._vault:
            if self._closed:
                return
            workflow.set_current_details(f"Terminó: {outcome}. {_details(self._progress)}")
            workflow.logger.info(
                "campaña %s: cierra %s — %s%s",
                self._campaign_id,
                outcome,
                _details(self._progress),
                f" — {reason}" if reason else "",
            )
            await workflow.execute_activity(
                "finish_campaign_send",
                args=[self._campaign_id, replace(self._progress, state=outcome), outcome, reason],
                start_to_close_timeout=_FAST,
                retry_policy=_VAULT_RETRY,
            )
            self._closed = True

    def _current(self) -> CampaignSendProgress:
        if self._cancel_requested:
            state = STATE_CANCELLING
        elif self._paused:
            state = STATUS_PAUSED
        else:
            state = STATUS_SENDING
        paused = state == STATUS_PAUSED
        return replace(
            self._progress,
            state=state,
            pause_reason=self._pause_reason if paused else None,
            resume_at_ms=self._resume_at_ms if paused else None,
        )

    @workflow.query
    def progress(self) -> CampaignSendProgress:
        return self._current()

    @workflow.update
    async def pause(self) -> CampaignSendProgress:
        """Frena después del lote en vuelo (ese termina). El vault lo muestra
        al momento."""
        self._paused = True
        workflow.logger.info("campaña %s: pausa pedida", self._campaign_id)
        await self._record_from_handler()
        return self._current()

    @pause.validator
    def _validate_pause(self) -> None:
        self._reject_if_closing()
        if self._paused:
            raise ValueError("El envío ya está pausado.")

    @workflow.update
    async def resume(self) -> CampaignSendProgress:
        self._paused = False
        self._pause_reason = None
        self._resume_at_ms = None
        workflow.logger.info("campaña %s: reanudación pedida", self._campaign_id)
        await self._record_from_handler()
        return self._current()

    @resume.validator
    def _validate_resume(self) -> None:
        self._reject_if_closing()
        if not self._paused:
            raise ValueError("El envío no está pausado.")

    @workflow.update
    async def cancel(self) -> CampaignSendProgress:
        """No sale ningún lote más; el que está en vuelo termina y la
        campaña queda cancelada con lo que alcanzó a salir."""
        self._cancel_requested = True
        workflow.logger.info("campaña %s: cancelación pedida", self._campaign_id)
        await self._record_from_handler()
        return self._current()

    @cancel.validator
    def _validate_cancel(self) -> None:
        self._reject_if_closing()

    async def _record_from_handler(self) -> None:
        """El pedido ya se aplicó: si el vault no responde, el envío lo graba
        en la próxima escritura y el Update no falla por eso."""
        try:
            await self._record_if_changed()
        except ActivityError:
            workflow.logger.exception(
                "campaña %s: no pude grabar el estado pedido; lo graba el próximo lote",
                self._campaign_id,
            )

    def _reject_if_closing(self) -> None:
        if self._done:
            raise ValueError("El envío ya terminó.")
        if self._cancel_requested:
            raise ValueError("El envío ya se está cancelando.")


def _summary(progress: CampaignSendProgress, outcome: str) -> dict:
    return {
        "planned": progress.total,
        "sent": progress.sent,
        "failed": progress.failed,
        "opted_out": progress.opted_out,
        "skipped": progress.skipped,
        "outcome": outcome,
    }


def _details(progress: CampaignSendProgress) -> str:
    return (
        f"{progress.processed} de {progress.total} procesados · {progress.sent} enviados · "
        f"{progress.failed} fallidos · {progress.opted_out} bajas · {progress.skipped} omitidos"
    )


_NOBODY_REASON = (
    "No había nadie para enviar: toda la audiencia quedó fuera al disparar "
    "(bajas, casos con una persona del equipo, respiro entre campañas u horario)."
)
_QUIET_REASON = (
    "Horario de silencio: la mayoría de este lote está fuera del horario permitido "
    "de los clientes. El envío sigue solo cuando abra."
)


def _epoch_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _innermost_message(exc: BaseException) -> str:
    cause: BaseException = exc
    while getattr(cause, "cause", None) is not None:
        cause = cause.cause  # type: ignore[attr-defined]
    return getattr(cause, "message", None) or str(cause) or type(cause).__name__


def _is_cancellation(exc: BaseException) -> bool:
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, (asyncio.CancelledError, CancelledError)):
            return True
        cause = getattr(cause, "cause", None)
    return False


def _failure_reason(exc: BaseException, progress: CampaignSendProgress) -> str:
    """El motivo que ve el operador: el error de más adentro (la activity que
    falló), no el envoltorio de Temporal."""
    cause: BaseException = exc
    while getattr(cause, "cause", None) is not None:
        cause = cause.cause  # type: ignore[attr-defined]
    message = getattr(cause, "message", None) or str(cause) or type(cause).__name__
    return (
        f"El envío se detuvo con {progress.processed} de {progress.total} destinatarios "
        f"procesados: {message}"
    )
