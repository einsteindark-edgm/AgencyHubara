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
        carousel: bool = False,
        blocked_reason: str | None = None,
    ) -> None:
        self.total = total
        self.hold_batch_at = hold_batch_at
        self.skip = skip
        self.failing = failing
        self.opted_out = opted_out
        self.stamp_failure = stamp_failure
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
        self.release = asyncio.Event()
        self.paused_recorded = asyncio.Event()

    def activities(self) -> list:
        fakes = self

        @activity.defn(name="prepare_campaign_send")
        async def prepare(campaign_id: str) -> CampaignSendStart:
            return CampaignSendStart(
                total=0 if fakes.blocked_reason else fakes.total,
                template_name="campaign_promo_marketing_v1",
                carousel=fakes.carousel,
                blocked_reason=fakes.blocked_reason,
            )

        @activity.defn(name="prepare_campaign_carousel")
        async def carousel(campaign_id: str) -> list[CarouselCard]:
            fakes.carousels.append(campaign_id)
            return fakes.cards

        @activity.defn(name="load_campaign_batch")
        async def load(campaign_id: str, start: int, end: int) -> CampaignBatch:
            fakes.loads.append((start, end))
            if start == fakes.hold_batch_at:
                fakes.holding.set()
                await fakes.release.wait()
            ids = [f"wa_{i}" for i in range(start, end)]
            return CampaignBatch(
                recipients=[
                    RecipientSendPlan(session_id=s, variables={"greeting": "Hola"})
                    for s in ids
                    if s not in fakes.skip
                ],
                skipped=[SkippedRecipient(s, "quiet_hours") for s in ids if s in fakes.skip],
            )

        @activity.defn(name="stamp_campaign_touch")
        async def stamp(
            session_id: str, campaign_id: str, campaign_name: str, wa_message_id: str | None = None
        ) -> None:
            if session_id == fakes.stamp_failure:
                raise ApplicationError("el vault no responde", non_retryable=True)
            fakes.touches.append((session_id, campaign_name, wa_message_id))

        @activity.defn(name="mark_marketing_opt_out")
        async def opt_out(session_id: str, campaign_id: str) -> None:
            fakes.opt_outs.append((session_id, campaign_id))

        @activity.defn(name="record_campaign_progress")
        async def record(
            campaign_id: str,
            progress: CampaignSendProgress,
            batch: CampaignBatchResult | None = None,
        ) -> None:
            fakes.recorded.append((progress, batch))
            if progress.state == "paused":
                fakes.paused_recorded.set()

        @activity.defn(name="finish_campaign_send")
        async def finish(
            campaign_id: str, progress: CampaignSendProgress, outcome: str, reason: str | None = None
        ) -> None:
            fakes.finished.append((progress, outcome, reason))

        return [prepare, carousel, load, stamp, opt_out, record, finish]

    def send_activity(self):
        fakes = self

        @activity.defn(name="send_whatsapp_template_activity")
        async def send(
            session_id: str,
            template_name: str,
            variables: dict,
            carousel_cards: list[CarouselCard] | None = None,
        ) -> dict:
            if session_id in fakes.failing:
                raise ApplicationError("Meta 131026 non-retryable", non_retryable=True)
            if session_id in fakes.opted_out:
                raise ApplicationError(
                    "WhatsApp template send failed (non-retryable, code=131050)",
                    non_retryable=True,
                    type="TemplateMetaError131050",
                )
            fakes.sends.append(session_id)
            fakes.send_cards.append(carousel_cards)
            return {"wa_message_id": f"wamid-{session_id}", "ok": True, "error": None}

        return send

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
        with pytest.raises(WorkflowUpdateFailedError):
            await handle.execute_update(CampaignSendWorkflowV2.pause)

        fakes.release.set()
        await _wait(fakes.paused_recorded)
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
async def test_a_failure_mid_send_leaves_the_campaign_failed_with_the_reason(
    temporal_env: WorkflowEnvironment,
) -> None:
    """Un stamp que no se puede escribir tumbaba el V1 y la campaña quedaba
    en «sending» para siempre: ahora queda fallida, con el motivo y con lo
    que alcanzó a salir."""
    fakes = Fakes(300, stamp_failure="wa_150")

    async with _workers(temporal_env, fakes):
        handle = await _start(temporal_env, batch_size=100)
        with pytest.raises(WorkflowFailureError):
            await handle.result()

    assert (200, 300) not in fakes.loads
    [(progress, outcome, reason)] = fakes.finished
    assert outcome == "failed"
    assert (progress.processed, progress.sent) == (100, 100)
    assert reason is not None and "el vault no responde" in reason


@pytest.mark.asyncio
async def test_opt_outs_failures_and_batch_skips_are_counted_apart(
    temporal_env: WorkflowEnvironment,
) -> None:
    fakes = Fakes(
        5,
        skip=frozenset({"wa_2"}),
        failing=frozenset({"wa_3"}),
        opted_out=frozenset({"wa_4"}),
    )

    summary = await _run(temporal_env, fakes)

    [batch] = fakes.batches
    assert batch.sent == 2
    assert batch.failed == ["wa_3"]
    assert batch.opted_out == ["wa_4"]
    assert batch.skipped == [SkippedRecipient("wa_2", "quiet_hours")]
    assert fakes.opt_outs == [("wa_4", "mkt-1")]
    # Solo lo que salió queda atribuido, con el id del mensaje de Meta.
    assert sorted(fakes.touches) == [
        ("wa_0", "Promo madre", "wamid-wa_0"),
        ("wa_1", "Promo madre", "wamid-wa_1"),
    ]
    progress = fakes.finished[0][0]
    assert (progress.sent, progress.failed, progress.opted_out, progress.skipped) == (2, 1, 1, 1)
    assert summary == {
        "planned": 5,
        "sent": 2,
        "failed": 1,
        "opted_out": 1,
        "skipped": 1,
        "outcome": "sent",
    }


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
