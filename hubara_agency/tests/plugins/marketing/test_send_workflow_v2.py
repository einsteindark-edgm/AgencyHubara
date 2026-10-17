"""CampaignSendWorkflowV2 — una campaña grande se envía por lotes y el
operador la controla mientras sale.

El V1 recorría a todos los destinatarios en una sola historia (≈12 eventos
por persona): cerca del destinatario 4.000 llegaba al límite de 51.200
eventos de Temporal, y el tope de importados es 5.000. Tampoco se podía
pausar ni cancelar a mitad de envío, y un error de stamp dejaba la campaña
en «sending» para siempre (auditoría de Temporal, 2026-10-15).

WorkflowEnvironment time-skipping + activities fake con tracker (R-DET). Los
sends corren en su propio task queue (el del ritmo hacia Meta).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from temporalio import activity
from temporalio.client import (
    WorkflowFailureError,
    WorkflowHandle,
    WorkflowUpdateFailedError,
)
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.marketing.agent.campaigns.workflows.send_v2 import (
    CampaignSendBatchWorkflow,
    CampaignSendWorkflowV2,
)
from src.plugins.marketing.domain.campaign_send import (
    CampaignBatch,
    CampaignBatchResult,
    CampaignDelivery,
    CampaignDeliveryOutcome,
    CampaignSendInput,
    CampaignSendProgress,
    CampaignSendStart,
    sends_task_queue,
)
from src.plugins.marketing.domain.campaigns import RecipientSendPlan, SkippedRecipient
from src.sdk import get_task_queue
from src.sdk.messagingkit import CarouselCard

QUEUE = get_task_queue("marketing", "campaigns")
WORKFLOW_ID = "campaign-send-mkt-1"
#: Límite duro de Temporal por ejecución.
HISTORY_EVENT_LIMIT = 51_200


class Fakes:
    """Activities fake + lo que vio cada una. `hold_batch_at`: el lote que
    empieza en ese índice se queda cargando hasta `release` (el operador
    actúa con un lote en vuelo)."""

    def __init__(
        self,
        total: int,
        *,
        hold_batch_at: int | None = None,
        skip: frozenset[str] = frozenset(),
        failing: frozenset[str] = frozenset(),
        opted_out: frozenset[str] = frozenset(),
        stamp_failure: str | None = None,
        quiet_now: frozenset[str] = frozenset(),
        ambiguous: frozenset[str] = frozenset(),
        load_failure_at: int | None = None,
        quiet_until_at: dict[int, int] | None = None,
        carousel_error: bool = False,
        carousel: bool = False,
        blocked_reason: str | None = None,
        hold_prepare: bool = False,
    ) -> None:
        self.total = total
        self.hold_prepare = hold_prepare
        self.hold_batch_at = hold_batch_at
        self.skip = skip
        self.failing = failing
        self.opted_out = opted_out
        self.stamp_failure = stamp_failure
        self.quiet_now = quiet_now
        self.ambiguous = ambiguous
        self.delivery_attempts: dict[str, int] = {}
        self.load_failure_at = load_failure_at
        self.quiet_until_at = dict(quiet_until_at or {})
        self.carousel_error = carousel_error
        self.carousel = carousel
        self.blocked_reason = blocked_reason
        self.cards = [
            CarouselCard(body_text="A · $1", product_retailer_id="HUB-A", catalog_id="868"),
            CarouselCard(body_text="B · $2", product_retailer_id="HUB-B", catalog_id="868"),
        ]
        self.loads: list[tuple[int, int]] = []
        self.sends: list[str] = []
        self.send_cards: list[list[CarouselCard] | None] = []
        self.touches: list[tuple[str, str, str | None]] = []
        self.opt_outs: list[tuple[str, str]] = []
        self.carousels: list[str] = []
        self.recorded: list[tuple[CampaignSendProgress, CampaignBatchResult | None]] = []
        self.finished: list[tuple[CampaignSendProgress, str, str | None]] = []
        self.holding = asyncio.Event()
        self.preparing = asyncio.Event()
        self.release = asyncio.Event()
        self.paused_recorded = asyncio.Event()
        self.batch_recorded = asyncio.Event()

    def activities(self) -> list:
        fakes = self

        @activity.defn(name="prepare_campaign_send")
        async def prepare(campaign_id: str) -> CampaignSendStart:
            if fakes.hold_prepare:
                fakes.preparing.set()
                await fakes.release.wait()
            return CampaignSendStart(
                total=0 if fakes.blocked_reason else fakes.total,
                template_name="campaign_promo_marketing_v1",
                carousel=fakes.carousel,
                blocked_reason=fakes.blocked_reason,
            )

        @activity.defn(name="prepare_campaign_carousel")
        async def carousel(campaign_id: str) -> list[CarouselCard]:
            fakes.carousels.append(campaign_id)
            if fakes.carousel_error:
                raise ApplicationError(
                    "Carrusel de la campaña 'mkt-1': producto 'vela' no está en el catálogo",
                    non_retryable=True,
                )
            return fakes.cards

        @activity.defn(name="load_campaign_batch")
        async def load(campaign_id: str, start: int, end: int) -> CampaignBatch:
            fakes.loads.append((start, end))
            if start == fakes.hold_batch_at:
                fakes.holding.set()
                await fakes.release.wait()
            if start == fakes.load_failure_at:
                fakes.load_failure_at = None  # falla una vez; el reintento anda
                raise ApplicationError("el vault no responde", non_retryable=True)
            quiet_until_ms = fakes.quiet_until_at.pop(start, None)
            ids = [f"wa_{i}" for i in range(start, end)]
            return CampaignBatch(
                recipients=[
                    RecipientSendPlan(session_id=s, variables={"greeting": "Hola"})
                    for s in ids
                    if s not in fakes.skip
                ],
                skipped=[SkippedRecipient(s, "quiet_hours") for s in ids if s in fakes.skip],
                touch={"campaign_id": campaign_id, "campaign_name": "Promo madre"},
                quiet_until_ms=quiet_until_ms,
            )

        @activity.defn(name="record_campaign_progress")
        async def record(
            campaign_id: str,
            progress: CampaignSendProgress,
            batch: CampaignBatchResult | None = None,
        ) -> None:
            fakes.recorded.append((progress, batch))
            if progress.state == "paused":
                fakes.paused_recorded.set()
            if batch is not None:
                fakes.batch_recorded.set()

        @activity.defn(name="finish_campaign_send")
        async def finish(
            campaign_id: str, progress: CampaignSendProgress, outcome: str, reason: str | None = None
        ) -> None:
            fakes.finished.append((progress, outcome, reason))

        return [prepare, carousel, load, record, finish]

    def send_activity(self):
        fakes = self

        @activity.defn(name="deliver_campaign_message")
        async def deliver(delivery: CampaignDelivery) -> CampaignDeliveryOutcome:
            sid = delivery.session_id
            fakes.delivery_attempts[sid] = fakes.delivery_attempts.get(sid, 0) + 1
            if sid in fakes.ambiguous:
                # Timeout o 504 de Meta: la plantilla pudo haber salido.
                raise ApplicationError("Meta no respondió a tiempo", type="TemplateSendAmbiguous")
            if sid in fakes.failing:
                raise ApplicationError(
                    "Meta 131026 non-retryable", non_retryable=True, type="TemplateMetaError131026"
                )
            if sid in fakes.opted_out:
                # Meta 131050: la activity marca la baja atribuida a la campaña.
                fakes.opt_outs.append((sid, delivery.campaign_id))
                return CampaignDeliveryOutcome(outcome="opted_out")
            if sid in fakes.quiet_now:
                return CampaignDeliveryOutcome(outcome="skipped", skip_reason="quiet_hours")
            fakes.sends.append(sid)
            fakes.send_cards.append(delivery.cards)
            stamped = sid != fakes.stamp_failure
            if stamped:
                fakes.touches.append((sid, delivery.touch["campaign_name"], f"wamid-{sid}"))
            return CampaignDeliveryOutcome(
                outcome="sent", wa_message_id=f"wamid-{sid}", stamped=stamped
            )

        return deliver

    @property
    def batches(self) -> list[CampaignBatchResult]:
        return [batch for _progress, batch in self.recorded if batch is not None]


@asynccontextmanager
async def _workers(env: WorkflowEnvironment, fakes: Fakes) -> AsyncIterator[None]:
    async with Worker(
        env.client,
        task_queue=QUEUE,
        workflows=[CampaignSendWorkflowV2, CampaignSendBatchWorkflow],
        activities=fakes.activities(),
    ), Worker(
        env.client,
        task_queue=sends_task_queue(QUEUE),
        activities=[fakes.send_activity()],
    ):
        yield


async def _wait(event: asyncio.Event) -> None:
    """Un rojo no puede colgar la suite: el evento llega o el test falla."""
    await asyncio.wait_for(event.wait(), timeout=30)


async def _start(env: WorkflowEnvironment, **over) -> WorkflowHandle:
    return await env.client.start_workflow(
        CampaignSendWorkflowV2.run,
        CampaignSendInput(campaign_id="mkt-1", campaign_name="Promo madre", **over),
        id=WORKFLOW_ID,
        task_queue=QUEUE,
    )


async def _run(env: WorkflowEnvironment, fakes: Fakes, **over) -> dict:
    async with _workers(env, fakes):
        return await (await _start(env, **over)).result()


async def _history_sizes(env: WorkflowEnvironment, handle: WorkflowHandle) -> list[int]:
    """Eventos de cada corrida de la cadena de continue-as-new."""
    sizes: list[int] = []
    run_id: str | None = handle.first_execution_run_id
    while run_id:
        history = await env.client.get_workflow_handle(WORKFLOW_ID, run_id=run_id).fetch_history()
        sizes.append(len(history.events))
        last = history.events[-1]
        run_id = (
            last.workflow_execution_continued_as_new_event_attributes.new_execution_run_id
            if last.HasField("workflow_execution_continued_as_new_event_attributes")
            else None
        )
    return sizes


@pytest.mark.asyncio
async def test_sends_every_recipient_batch_by_batch_and_records_each_batch(
    temporal_env: WorkflowEnvironment,
) -> None:
    fakes = Fakes(250)

    summary = await _run(temporal_env, fakes, batch_size=100)

    assert fakes.loads == [(0, 100), (100, 200), (200, 250)]
    assert sorted(fakes.sends) == sorted(f"wa_{i}" for i in range(250))
    assert [(b.start, b.end, b.sent) for b in fakes.batches] == [
        (0, 100, 100),
        (100, 200, 100),
        (200, 250, 50),
    ]
    # El vault ve el avance: arranca en 0 y sube lote a lote.
    assert [p.processed for p, _batch in fakes.recorded] == [0, 100, 200, 250]
    [(progress, outcome, reason)] = fakes.finished
    assert (outcome, reason) == ("sent", None)
    assert (progress.total, progress.processed, progress.sent) == (250, 250, 250)
    assert summary["sent"] == 250
    assert summary["outcome"] == "sent"


@pytest.mark.asyncio
async def test_six_thousand_recipients_never_get_near_the_history_limit(
    temporal_env: WorkflowEnvironment,
) -> None:
    """El caso que rompía el V1: más destinatarios que el tope de importados.
    Ninguna historia (padre por corrida, ni lote) se acerca al límite."""
    fakes = Fakes(6_000)

    async with _workers(temporal_env, fakes):
        handle = await _start(temporal_env)
        summary = await handle.result()
        run_sizes = await _history_sizes(temporal_env, handle)
        batch_sizes = [
            len((await temporal_env.client.get_workflow_handle(
                f"{WORKFLOW_ID}-batch-{start:07d}"
            ).fetch_history()).events)
            for start in range(0, 6_000, 100)
        ]

    assert len(set(fakes.sends)) == 6_000
    assert summary["sent"] == 6_000
    assert fakes.finished[0][0].sent == 6_000
    # El padre se renovó con continue-as-new (2.000 destinatarios por corrida).
    assert len(run_sizes) == 3
    assert max(run_sizes) < HISTORY_EVENT_LIMIT // 10
    assert max(batch_sizes) < HISTORY_EVENT_LIMIT // 10


@pytest.mark.asyncio
async def test_pause_waits_after_the_batch_in_flight_and_resume_finishes_the_rest(
    temporal_env: WorkflowEnvironment,
) -> None:
    fakes = Fakes(300, hold_batch_at=0)

    async with _workers(temporal_env, fakes):
        handle = await _start(temporal_env, batch_size=100)
        await _wait(fakes.holding)
        # Reanudar algo que no está pausado no tiene sentido: se rechaza.
        with pytest.raises(WorkflowUpdateFailedError):
            await handle.execute_update(CampaignSendWorkflowV2.resume)

        paused = await handle.execute_update(CampaignSendWorkflowV2.pause)
        assert paused.state == "paused"
        # El panel lo ve YA (el lote en vuelo sigue): no vuelve a ofrecer
        # «Pausar» mientras ese lote termina.
        assert fakes.recorded[-1] == (paused, None)
        with pytest.raises(WorkflowUpdateFailedError):
            await handle.execute_update(CampaignSendWorkflowV2.pause)

        fakes.release.set()
        await _wait(fakes.batch_recorded)
        progress = await handle.query(CampaignSendWorkflowV2.progress)
        assert (progress.processed, progress.sent, progress.state) == (100, 100, "paused")
        assert fakes.loads == [(0, 100)]

        resumed = await handle.execute_update(CampaignSendWorkflowV2.resume)
        assert resumed.state == "sending"
        summary = await handle.result()

    assert summary["sent"] == 300
    assert fakes.loads == [(0, 100), (100, 200), (200, 300)]
    states = [p.state for p, _batch in fakes.recorded]
    assert "paused" in states
    assert states[-1] == "sending"
    assert fakes.finished[0][1] == "sent"


@pytest.mark.asyncio
async def test_cancel_mid_send_stops_after_the_batch_in_flight_and_closes_the_campaign(
    temporal_env: WorkflowEnvironment,
) -> None:
    fakes = Fakes(300, hold_batch_at=100)

    async with _workers(temporal_env, fakes):
        handle = await _start(temporal_env, batch_size=100)
        await _wait(fakes.holding)

        cancelling = await handle.execute_update(CampaignSendWorkflowV2.cancel)
        assert cancelling.state == "cancelling"
        assert fakes.recorded[-1] == (cancelling, None)
        with pytest.raises(WorkflowUpdateFailedError):
            await handle.execute_update(CampaignSendWorkflowV2.cancel)
        fakes.release.set()
        summary = await handle.result()

    assert fakes.loads == [(0, 100), (100, 200)]
    assert len(fakes.sends) == 200
    [(progress, outcome, reason)] = fakes.finished
    assert outcome == "cancelled"
    assert (progress.processed, progress.sent) == (200, 200)
    assert summary["outcome"] == "cancelled"
    assert summary["sent"] == 200


@pytest.mark.asyncio
async def test_cancel_while_paused_closes_without_sending_more(
    temporal_env: WorkflowEnvironment,
) -> None:
    fakes = Fakes(300, hold_batch_at=0)

    async with _workers(temporal_env, fakes):
        handle = await _start(temporal_env, batch_size=100)
        await _wait(fakes.holding)
        await handle.execute_update(CampaignSendWorkflowV2.pause)
        fakes.release.set()
        await _wait(fakes.paused_recorded)

        await handle.execute_update(CampaignSendWorkflowV2.cancel)
        summary = await handle.result()

    assert fakes.loads == [(0, 100)]
    assert fakes.finished[0][1] == "cancelled"
    assert summary["sent"] == 100


@pytest.mark.asyncio
async def test_a_temporal_cancellation_mid_send_still_closes_the_campaign(
    temporal_env: WorkflowEnvironment,
) -> None:
    """Cancelado desde la UI de Temporal (no por el botón): la campaña no
    puede quedar en «sending» para siempre."""
    fakes = Fakes(300, hold_batch_at=100)

    async with _workers(temporal_env, fakes):
        handle = await _start(temporal_env, batch_size=100)
        await _wait(fakes.holding)
        await handle.cancel()
        with pytest.raises(WorkflowFailureError):
            await handle.result()
        fakes.release.set()

    [(progress, outcome, _reason)] = fakes.finished
    assert outcome == "cancelled"
    assert progress.processed == 100


@pytest.mark.asyncio
async def test_cancelling_before_the_send_starts_does_not_close_the_campaign(
    temporal_env: WorkflowEnvironment,
) -> None:
    """Cancelar una campaña programada (antes de dispararse) la devuelve a
    borrador desde la API. Si el workflow la cerrara como «cancelada», el
    operador ya no podría editarla ni reprogramarla."""
    fakes = Fakes(300, hold_prepare=True)

    async with _workers(temporal_env, fakes):
        handle = await _start(temporal_env, batch_size=100)
        await _wait(fakes.preparing)
        await handle.cancel()
        with pytest.raises(WorkflowFailureError):
            await handle.result()
        fakes.release.set()

    assert (fakes.recorded, fakes.finished, fakes.loads) == ([], [], [])


@pytest.mark.asyncio
async def test_a_touch_that_could_not_be_written_does_not_stop_the_campaign(
    temporal_env: WorkflowEnvironment,
) -> None:
    """Lo que ya salió no se reintenta ni frena la campaña por no poder
    anotarlo: cuenta como enviado y queda en la lista de sin atribución."""
    fakes = Fakes(300, stamp_failure="wa_150")

    summary = await _run(temporal_env, fakes, batch_size=100)

    assert summary["sent"] == 300
    assert fakes.finished[0][1] == "sent"
    assert [b.unstamped for b in fakes.batches] == [[], ["wa_150"], []]


@pytest.mark.asyncio
async def test_a_batch_that_fails_pauses_the_campaign_with_the_reason_and_resume_retries_it(
    temporal_env: WorkflowEnvironment,
) -> None:
    """Un error que tumbaba el V1 dejaba la campaña en «sending» para
    siempre. Un lote que falla (el vault no responde) ahora PAUSA la campaña
    con el motivo a la vista: «fallida» no tiene vuelta y perdía al resto de
    la audiencia. Reanudar vuelve a intentar ese lote."""
    fakes = Fakes(300, load_failure_at=100)

    async with _workers(temporal_env, fakes):
        handle = await _start(temporal_env, batch_size=100)
        await _wait(fakes.paused_recorded)
        paused = fakes.recorded[-1][0]
        assert (paused.state, paused.processed) == ("paused", 100)
        assert paused.pause_reason is not None and "el vault no responde" in paused.pause_reason
        assert fakes.finished == []

        await handle.execute_update(CampaignSendWorkflowV2.resume)
        summary = await handle.result()

    assert fakes.loads == [(0, 100), (100, 200), (100, 200), (200, 300)]
    assert summary["sent"] == 300
    assert fakes.recorded[-1][0].pause_reason is None


@pytest.mark.asyncio
async def test_a_failure_outside_the_batches_leaves_the_campaign_failed_with_the_reason(
    temporal_env: WorkflowEnvironment,
) -> None:
    fakes = Fakes(300, carousel=True, carousel_error=True)

    async with _workers(temporal_env, fakes):
        handle = await _start(temporal_env, batch_size=100)
        with pytest.raises(WorkflowFailureError):
            await handle.result()

    assert fakes.loads == []
    [(progress, outcome, reason)] = fakes.finished
    assert outcome == "failed"
    assert reason is not None and "no está en el catálogo" in reason


@pytest.mark.asyncio
async def test_when_meta_rejects_most_of_a_batch_the_campaign_pauses_instead_of_burning_the_audience(
    temporal_env: WorkflowEnvironment,
) -> None:
    """Plantilla pausada, token vencido, pago: Meta rechaza casi todo. Seguir
    a 10/s quemaba a toda la audiencia como «fallidos» sin vuelta."""
    fakes = Fakes(300, failing=frozenset(f"wa_{i}" for i in range(100, 160)))

    async with _workers(temporal_env, fakes):
        handle = await _start(temporal_env, batch_size=100)
        await _wait(fakes.paused_recorded)
        paused = fakes.recorded[-1][0]
        assert paused.processed == 200
        assert "60 de 100" in paused.pause_reason
        assert "TemplateMetaError131026" in paused.pause_reason
        assert (200, 300) not in fakes.loads

        await handle.execute_update(CampaignSendWorkflowV2.resume)
        summary = await handle.result()

    assert summary["sent"] == 240
    assert fakes.loads[-1] == (200, 300)


@pytest.mark.asyncio
async def test_a_batch_mostly_in_quiet_hours_waits_for_the_window_instead_of_skipping_them(
    temporal_env: WorkflowEnvironment,
) -> None:
    """Una campaña disparada a las 21:15 no quema la cola marcándola «fuera
    de horario»: espera a que abra la ventana y sigue sola."""
    import time

    opens_at = int(time.time() * 1000) + 10 * 3_600_000
    fakes = Fakes(300, quiet_until_at={100: opens_at})

    async with _workers(temporal_env, fakes):
        handle = await _start(temporal_env, batch_size=100)
        summary = await handle.result()

    assert fakes.loads == [(0, 100), (100, 200), (100, 200), (200, 300)]
    assert summary["sent"] == 300
    waiting = [p for p, _batch in fakes.recorded if p.resume_at_ms is not None]
    assert waiting and waiting[0].state == "paused"
    assert waiting[0].resume_at_ms == opens_at
    assert "horario" in waiting[0].pause_reason
    assert fakes.finished[0][1] == "sent"


@pytest.mark.asyncio
async def test_a_campaign_paused_for_a_week_cancels_itself_with_the_reason(
    temporal_env: WorkflowEnvironment,
) -> None:
    fakes = Fakes(300, hold_batch_at=0)

    async with _workers(temporal_env, fakes):
        handle = await _start(temporal_env, batch_size=100)
        await _wait(fakes.holding)
        await handle.execute_update(CampaignSendWorkflowV2.pause)
        fakes.release.set()
        summary = await handle.result()

    assert summary["outcome"] == "cancelled"
    [(progress, outcome, reason)] = fakes.finished
    assert (outcome, progress.processed) == ("cancelled", 100)
    assert reason is not None and "7 días" in reason


@pytest.mark.asyncio
async def test_a_campaign_with_nobody_to_send_to_closes_failed_with_the_reason(
    temporal_env: WorkflowEnvironment,
) -> None:
    """Toda la audiencia quedó fuera al disparar (bajas, respiro…): «Enviada»
    con 0 escondía que no salió nada."""
    fakes = Fakes(0)

    summary = await _run(temporal_env, fakes)

    [(progress, outcome, reason)] = fakes.finished
    assert outcome == "failed"
    assert reason is not None and "nadie" in reason
    assert summary["outcome"] == "failed"


@pytest.mark.asyncio
async def test_opt_outs_failures_and_batch_skips_are_counted_apart(
    temporal_env: WorkflowEnvironment,
) -> None:
    fakes = Fakes(
        6,
        skip=frozenset({"wa_2"}),
        failing=frozenset({"wa_3"}),
        opted_out=frozenset({"wa_4"}),
        quiet_now=frozenset({"wa_5"}),
    )

    summary = await _run(temporal_env, fakes)

    [batch] = fakes.batches
    assert batch.sent == 2
    assert batch.failed == ["wa_3"]
    assert batch.opted_out == ["wa_4"]
    # Omitido al cargar el lote (wa_2) y al momento de enviar (wa_5).
    assert batch.skipped == [
        SkippedRecipient("wa_2", "quiet_hours"),
        SkippedRecipient("wa_5", "quiet_hours"),
    ]
    assert batch.failure_types == {"TemplateMetaError131026": 1}
    assert batch.failure_example is not None and "131026" in batch.failure_example
    assert fakes.opt_outs == [("wa_4", "mkt-1")]
    # Solo lo que salió queda atribuido, con el id del mensaje de Meta.
    assert sorted(fakes.touches) == [
        ("wa_0", "Promo madre", "wamid-wa_0"),
        ("wa_1", "Promo madre", "wamid-wa_1"),
    ]
    progress = fakes.finished[0][0]
    assert (progress.sent, progress.failed, progress.opted_out, progress.skipped) == (2, 1, 1, 2)
    assert summary == {
        "planned": 6,
        "sent": 2,
        "failed": 1,
        "opted_out": 1,
        "skipped": 2,
        "outcome": "sent",
    }


@pytest.mark.asyncio
async def test_an_ambiguous_send_is_not_retried_so_nobody_gets_the_promo_twice(
    temporal_env: WorkflowEnvironment,
) -> None:
    """Timeout o 504 de Meta en la plantilla: pudo haber salido, y el touch no
    se escribió, así que el reintento la mandaría otra vez. Cuenta como
    fallido con su tipo; el cortacircuito lo ve si es masivo."""
    fakes = Fakes(3, ambiguous=frozenset({"wa_1"}))

    await _run(temporal_env, fakes)

    assert fakes.delivery_attempts["wa_1"] == 1
    [batch] = fakes.batches
    assert batch.failed == ["wa_1"]
    assert batch.failure_types == {"TemplateSendAmbiguous": 1}
    assert batch.sent == 2


@pytest.mark.asyncio
async def test_the_carousel_is_prepared_once_and_goes_to_every_recipient(
    temporal_env: WorkflowEnvironment,
) -> None:
    fakes = Fakes(150, carousel=True)

    await _run(temporal_env, fakes, batch_size=100)

    assert fakes.carousels == ["mkt-1"]
    assert len(fakes.send_cards) == 150
    assert all(cards == fakes.cards for cards in fakes.send_cards)


@pytest.mark.asyncio
async def test_a_plan_blocked_by_the_coupon_sends_nothing_and_does_not_touch_the_campaign(
    temporal_env: WorkflowEnvironment,
) -> None:
    """Al dispararse, el cupón ya no servía: la activity dejó la campaña
    fallida con el motivo; el workflow no manda nada ni la pisa."""
    fakes = Fakes(10, blocked_reason="No se envió: el cupón MAMA15 está pausado en Medusa.")

    summary = await _run(temporal_env, fakes)

    assert summary["blocked_reason"] == fakes.blocked_reason
    assert (fakes.loads, fakes.sends, fakes.recorded, fakes.finished) == ([], [], [], [])
