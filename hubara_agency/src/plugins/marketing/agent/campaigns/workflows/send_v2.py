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
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, CancelledError

with workflow.unsafe.imports_passed_through():
    from src.plugins.marketing.agent.campaigns.activities import PLAN_MAX_ATTEMPTS
    from src.plugins.marketing.domain.campaign_send import (
        SEND_CONCURRENCY,
        STATE_CANCELLING,
        CampaignBatch,
        CampaignBatchInput,
        CampaignBatchResult,
        CampaignSendInput,
        CampaignSendProgress,
        CampaignSendResume,
        CampaignSendStart,
        advance_progress,
        sends_task_queue,
    )
    from src.plugins.marketing.domain.campaigns import (
        STATUS_CANCELLED,
        STATUS_FAILED,
        STATUS_PAUSED,
        STATUS_SENDING,
        STATUS_SENT,
        RecipientSendPlan,
    )
    from src.sdk.messagingkit import CarouselCard, is_meta_opt_out_failure

_FAST = timedelta(seconds=30)
#: Arma la audiencia escaneando el vault. Sin heartbeat timeout: el escaneo es
#: síncrono y frena el loop del heartbeat, así que uno largo fallaría en falso.
_PLAN_TIMEOUT = timedelta(minutes=5)
#: El send real hace un POST a Graph con retries de red adentro del client.
#: Sin schedule-to-start: con el ritmo del queue de envíos, esperar turno no
#: es un error.
_SEND_TIMEOUT = timedelta(seconds=60)
_CAROUSEL_TIMEOUT = timedelta(seconds=60)
#: Retries acotados por destinatario: un número inválido no debe colgar la
#: campaña (los non-retryable de Meta cortan solos en el 1er intento).
_SEND_RETRY = RetryPolicy(maximum_attempts=3)
#: Escrituras al vault: idempotentes (el stamp por campaña+contacto, el lote
#: por su inicio), así que reintentar es seguro.
_VAULT_RETRY = RetryPolicy(maximum_attempts=5)

_SENT = "sent"
_FAILED = "failed"
_OPTED_OUT = "opted_out"


@workflow.defn(name="CampaignSendBatchWorkflow")
class CampaignSendBatchWorkflow:
    """Un lote: carga sus destinatarios (re-chequeados ahora), envía y
    estampa. Su historia queda acotada por el tamaño del lote."""

    @workflow.run
    async def run(self, inp: CampaignBatchInput) -> CampaignBatchResult:
        batch = await workflow.execute_activity(
            "load_campaign_batch",
            args=[inp.campaign_id, inp.start, inp.end],
            result_type=CampaignBatch,
            start_to_close_timeout=_FAST,
            retry_policy=_VAULT_RETRY,
        )
        sends_queue = sends_task_queue(workflow.info().task_queue)
        outcomes: list[str] = []
        for i in range(0, len(batch.recipients), SEND_CONCURRENCY):
            chunk = batch.recipients[i : i + SEND_CONCURRENCY]
            outcomes.extend(
                await asyncio.gather(
                    *(self._deliver(inp, recipient, sends_queue) for recipient in chunk)
                )
            )
        by_outcome = list(zip(batch.recipients, outcomes, strict=True))
        return CampaignBatchResult(
            start=inp.start,
            end=inp.end,
            sent=outcomes.count(_SENT),
            failed=[r.session_id for r, o in by_outcome if o == _FAILED],
            opted_out=[r.session_id for r, o in by_outcome if o == _OPTED_OUT],
            skipped=batch.skipped,
        )

    async def _deliver(
        self, inp: CampaignBatchInput, recipient: RecipientSendPlan, sends_queue: str
    ) -> str:
        send_args: list = [recipient.session_id, inp.template_name, recipient.variables]
        if inp.cards:
            send_args.append(inp.cards)
        try:
            outcome = await workflow.execute_activity(
                "send_whatsapp_template_activity",
                args=send_args,
                task_queue=sends_queue,
                start_to_close_timeout=_SEND_TIMEOUT,
                retry_policy=_SEND_RETRY,
            )
        except ActivityError as e:
            cause = e.cause
            if isinstance(cause, ApplicationError) and is_meta_opt_out_failure(cause.type):
                # Baja hecha en WhatsApp (131050): se registra atribuida a
                # ESTA campaña; no es un fallo ni se le vuelve a intentar.
                await workflow.execute_activity(
                    "mark_marketing_opt_out",
                    args=[recipient.session_id, inp.campaign_id],
                    start_to_close_timeout=_FAST,
                    retry_policy=_VAULT_RETRY,
                )
                return _OPTED_OUT
            return _FAILED
        # El id del mensaje va al touch: por él el webhook de estados anota
        # entregado, leído y precio de ESTA campaña (Ads, 2026-09-25).
        wa_message_id = outcome.get("wa_message_id") if isinstance(outcome, dict) else None
        await workflow.execute_activity(
            "stamp_campaign_touch",
            args=[recipient.session_id, inp.campaign_id, inp.campaign_name, wa_message_id],
            start_to_close_timeout=_FAST,
            retry_policy=_VAULT_RETRY,
        )
        return _SENT


@workflow.defn(name="CampaignSendWorkflowV2")
class CampaignSendWorkflowV2:
    def __init__(self) -> None:
        self._progress = CampaignSendProgress(total=0)
        self._paused = False
        self._cancel_requested = False
        self._done = False
        self._recorded_state: str | None = None

    @workflow.run
    async def run(self, inp: CampaignSendInput) -> dict:
        try:
            return await self._send(inp)
        except (asyncio.CancelledError, Exception) as exc:
            # Nunca «sending» colgada: cancelado desde Temporal (llega directo,
            # o envuelto en el error del lote hijo que se canceló con él) o
            # cualquier otro error cierra la campaña con el motivo.
            self._done = True
            if _is_cancellation(exc):
                await self._finish(inp, STATUS_CANCELLED, "Se canceló el envío desde Temporal.")
            else:
                await self._finish(inp, STATUS_FAILED, _failure_reason(exc, self._progress))
            raise

    async def _send(self, inp: CampaignSendInput) -> dict:
        if inp.resume is None:
            start = await workflow.execute_activity(
                "prepare_campaign_send",
                inp.campaign_id,
                result_type=CampaignSendStart,
                start_to_close_timeout=_PLAN_TIMEOUT,
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
            await self._record(inp, None)
        else:
            template_name = inp.resume.template_name
            cards = inp.resume.cards
            self._progress = inp.resume.progress
            self._paused = inp.resume.progress.paused
            self._recorded_state = inp.resume.progress.state

        batches_this_run = 0
        while self._progress.processed < self._progress.total:
            if self._paused and not self._cancel_requested:
                await self._wait_while_paused(inp)
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
            self._progress = advance_progress(self._progress, result)
            batches_this_run += 1
            await self._record(inp, result)

        self._done = True
        outcome = STATUS_CANCELLED if self._cancel_requested else STATUS_SENT
        await self._finish(inp, outcome, None)
        return _summary(self._progress, outcome)

    async def _wait_while_paused(self, inp: CampaignSendInput) -> None:
        if self._recorded_state != STATUS_PAUSED:
            await self._record(inp, None)
        await workflow.wait_condition(lambda: not self._paused or self._cancel_requested)
        if not self._cancel_requested:
            await self._record(inp, None)

    async def _record(self, inp: CampaignSendInput, batch: CampaignBatchResult | None) -> None:
        progress = self._current()
        workflow.set_current_details(_details(progress))
        await workflow.execute_activity(
            "record_campaign_progress",
            args=[inp.campaign_id, progress, batch],
            start_to_close_timeout=_FAST,
            retry_policy=_VAULT_RETRY,
        )
        self._recorded_state = progress.state

    async def _finish(self, inp: CampaignSendInput, outcome: str, reason: str | None) -> None:
        workflow.set_current_details(f"Terminó: {outcome}. {_details(self._progress)}")
        await workflow.execute_activity(
            "finish_campaign_send",
            args=[inp.campaign_id, replace(self._progress, state=outcome), outcome, reason],
            start_to_close_timeout=_FAST,
            retry_policy=_VAULT_RETRY,
        )

    def _current(self) -> CampaignSendProgress:
        if self._cancel_requested:
            state = STATE_CANCELLING
        elif self._paused:
            state = STATUS_PAUSED
        else:
            state = STATUS_SENDING
        return replace(self._progress, state=state)

    @workflow.query
    def progress(self) -> CampaignSendProgress:
        return self._current()

    @workflow.update
    def pause(self) -> CampaignSendProgress:
        """Frena después del lote en vuelo (ese termina)."""
        self._paused = True
        return self._current()

    @pause.validator
    def _validate_pause(self) -> None:
        self._reject_if_closing()
        if self._paused:
            raise ValueError("El envío ya está pausado.")

    @workflow.update
    def resume(self) -> CampaignSendProgress:
        self._paused = False
        return self._current()

    @resume.validator
    def _validate_resume(self) -> None:
        self._reject_if_closing()
        if not self._paused:
            raise ValueError("El envío no está pausado.")

    @workflow.update
    def cancel(self) -> CampaignSendProgress:
        """No sale ningún lote más; el que está en vuelo termina y la
        campaña queda cancelada con lo que alcanzó a salir."""
        self._cancel_requested = True
        return self._current()

    @cancel.validator
    def _validate_cancel(self) -> None:
        self._reject_if_closing()

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
