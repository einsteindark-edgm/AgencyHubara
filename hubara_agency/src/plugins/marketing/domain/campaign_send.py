"""Envío de campañas por lotes (CampaignSendWorkflowV2) — lógica PURA + DTOs.

Sin I/O, sin Temporal: el workflow orquesta y las activities leen/escriben el
vault; acá viven las reglas que se re-evalúan en cada lote y los DTOs que
cruzan workflow↔activity (R-JSON).
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.plugins.marketing.domain.campaigns import (
    STATUS_PAUSED,
    STATUS_SENDING,
    CampaignSendPlan,
    RecipientSendPlan,
    SkippedRecipient,
    excluded_recipient,
    segment_for_metadata,
)
from src.sdk.messagingkit import CarouselCard

#: Destinatarios por lote (un workflow hijo cada uno): la historia de un lote
#: queda en ~1.500 eventos y pausar/cancelar espera a lo sumo un lote.
BATCH_SIZE = 100
#: Lotes por corrida antes de continue-as-new: la historia del padre se
#: renueva con el cursor y los contadores (2.000 destinatarios por corrida).
BATCHES_PER_RUN = 20
#: Envíos en vuelo a la vez dentro de un lote. El ritmo real hacia Meta lo
#: pone el task queue de envíos (`max_task_queue_activities_per_second`).
SEND_CONCURRENCY = 10


def sends_task_queue(campaigns_queue: str) -> str:
    """El task queue propio de los envíos a Meta, al lado del de campañas:
    el límite de ritmo del queue frena solo los sends, no el plan ni el
    vault."""
    return f"{campaigns_queue}-sends"


@dataclass(frozen=True)
class CampaignSendStart:
    """El plan ya guardado en el vault: el workflow solo recibe el conteo."""

    total: int
    template_name: str = ""
    carousel: bool = False
    #: El envío NO sale (el cupón ya no servía al disparar); la campaña ya
    #: quedó fallida con este motivo.
    blocked_reason: str | None = None


#: Estado del envío en curso entre el «Cancelar» y el cierre (el lote en
#: vuelo termina). Los demás son los de la campaña: `sending` / `paused`.
STATE_CANCELLING = "cancelling"


@dataclass(frozen=True)
class CampaignSendProgress:
    """Por dónde va el envío: lo que devuelve la query y guarda el vault."""

    total: int
    processed: int = 0
    sent: int = 0
    failed: int = 0
    opted_out: int = 0
    skipped: int = 0
    #: `sending` | `paused` | `cancelling` (y el desenlace al cerrar).
    state: str = STATUS_SENDING

    @property
    def paused(self) -> bool:
        return self.state == STATUS_PAUSED


@dataclass(frozen=True)
class CampaignBatchInput:
    """Un lote: los destinatarios `[start, end)` del plan guardado."""

    campaign_id: str
    campaign_name: str
    start: int
    end: int
    template_name: str
    cards: list[CarouselCard] = field(default_factory=list)


@dataclass(frozen=True)
class CampaignSendResume:
    """Lo que viaja de una corrida a la siguiente (continue-as-new)."""

    progress: CampaignSendProgress
    template_name: str
    cards: list[CarouselCard] = field(default_factory=list)


@dataclass(frozen=True)
class CampaignSendInput:
    campaign_id: str
    campaign_name: str
    batch_size: int = BATCH_SIZE
    batches_per_run: int = BATCHES_PER_RUN
    #: None = arranque (arma el plan); con valor = viene de continue-as-new.
    resume: CampaignSendResume | None = None


@dataclass(frozen=True)
class CampaignBatch:
    """Los destinatarios de un lote que siguen pudiendo recibir la campaña."""

    recipients: list[RecipientSendPlan]
    skipped: list[SkippedRecipient] = field(default_factory=list)


def recheck_batch(
    recipients: list[RecipientSendPlan],
    metadata_by_session: dict[str, dict[str, Any]],
    *,
    is_quiet_hours: Callable[[str], bool],
) -> CampaignBatch:
    """Re-evalúa, al enviar ESTE lote, lo que pudo cambiar desde el plan.

    Baja o caso en manos de un humano (el metadata de ahora) y horario de
    silencio (la hora de ahora del cliente) frenan al contacto con la misma
    razón que el plan. Un contacto importado sin conversación no tiene
    metadata: no hay nada que lo frene salvo su horario.
    """
    to_send: list[RecipientSendPlan] = []
    skipped: list[SkippedRecipient] = []
    for recipient in recipients:
        metadata = metadata_by_session.get(recipient.session_id) or {}
        if segment_for_metadata(metadata) is None:
            skipped.append(excluded_recipient(recipient.session_id, metadata))
        elif is_quiet_hours(recipient.session_id):
            skipped.append(SkippedRecipient(recipient.session_id, "quiet_hours"))
        else:
            to_send.append(recipient)
    return CampaignBatch(recipients=to_send, skipped=skipped)


@dataclass(frozen=True)
class CampaignBatchResult:
    """Lo que pasó con los destinatarios `[start, end)` del plan."""

    start: int
    end: int
    sent: int = 0
    failed: list[str] = field(default_factory=list)
    opted_out: list[str] = field(default_factory=list)
    skipped: list[SkippedRecipient] = field(default_factory=list)


def advance_progress(
    progress: CampaignSendProgress, batch: CampaignBatchResult
) -> CampaignSendProgress:
    """El avance después de un lote: el cursor salta a su fin y se suman
    sus conteos."""
    return CampaignSendProgress(
        total=progress.total,
        processed=batch.end,
        sent=progress.sent + batch.sent,
        failed=progress.failed + len(batch.failed),
        opted_out=progress.opted_out + len(batch.opted_out),
        skipped=progress.skipped + len(batch.skipped),
        state=progress.state,
    )


def campaign_send_result(
    plan: CampaignSendPlan | None, batches: list[CampaignBatchResult]
) -> dict[str, Any]:
    """El `send_result` de la campaña (mismo shape que el V1 + `processed`):
    lo que se planeó, lo que se alcanzó a procesar y qué pasó con cada uno.
    Los omitidos son los del plan más los que cada lote frenó al enviar."""
    unit = plan.unit_cost_usd_micros if plan is not None else 0
    sent = sum(b.sent for b in batches)
    skipped = (plan.skipped if plan is not None else []) + [s for b in batches for s in b.skipped]
    return {
        "planned": len(plan.recipients) if plan is not None else 0,
        "processed": sum(b.end - b.start for b in batches),
        "sent": sent,
        "failed": [s for b in batches for s in b.failed],
        "opted_out": [s for b in batches for s in b.opted_out],
        "skipped": [{"session_id": s.session_id, "reason": s.reason} for s in skipped],
        "unit_cost_usd_micros": unit,
        "spent_usd_micros": unit * sent,
    }
