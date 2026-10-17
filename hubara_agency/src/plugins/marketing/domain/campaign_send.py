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
    has_recent_touch_from_other_campaign,
    received_campaign,
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


#: Razón de skip: el contacto ya recibió el envío real de esta campaña (un
#: lote que se volvió a correr no se la reenvía).
SKIP_YA_RECIBIDA = "ya_recibida"

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
    #: Por qué se pausó sola (lote que falló, Meta rechazando casi todo,
    #: horario de silencio); None si la pausó el operador o no está pausada.
    pause_reason: str | None = None
    #: Pausada por horario de silencio: cuándo sigue sola (epoch ms).
    resume_at_ms: int | None = None

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
    #: Lo que recibe el cliente (mensaje, cupón, productos), armado UNA vez
    #: por lote: el touch de cada envío lo copia (sin releer la campaña).
    touch: dict[str, Any] = field(default_factory=dict)
    #: La mayoría del lote está en su horario de silencio: el lote no sale y
    #: el envío espera hasta esta hora (epoch ms) para volver a intentarlo.
    quiet_until_ms: int | None = None


#: Desenlaces de un envío (`CampaignDeliveryOutcome.outcome`).
DELIVERY_SENT = "sent"
DELIVERY_OPTED_OUT = "opted_out"
DELIVERY_SKIPPED = "skipped"


@dataclass(frozen=True)
class CampaignDelivery:
    """Un envío de la campaña a UN contacto (activity `deliver_campaign_message`)."""

    campaign_id: str
    session_id: str
    template_name: str
    variables: dict[str, str]
    touch: dict[str, Any]
    cards: list[CarouselCard] = field(default_factory=list)
    bypass_cooldown: bool = False


@dataclass(frozen=True)
class CampaignDeliveryOutcome:
    outcome: str
    wa_message_id: str | None = None
    #: Para `skipped`: por qué no salió (la misma razón que el plan).
    skip_reason: str | None = None
    #: Salió pero no se pudo anotar el touch: cuenta como enviado igual (lo
    #: que ya salió no se reintenta por no poder anotarlo).
    stamped: bool = True


def recheck_batch(
    recipients: list[RecipientSendPlan],
    metadata_by_session: dict[str, dict[str, Any]],
    *,
    campaign_id: str,
    is_quiet_hours: Callable[[str], bool],
    now_ms: int | None = None,
) -> CampaignBatch:
    """Re-evalúa, al enviar ESTE lote, lo que pudo cambiar desde el plan.

    Primero: si el contacto ya tiene el touch real de esta campaña, no se le
    reenvía (un lote que se vuelve a correr — un Reset desde la UI de
    Temporal — no duplica: la dedup del send de plataforma dura 2 minutos).

    Baja o caso en manos de un humano (el metadata de ahora) y horario de
    silencio (la hora de ahora del cliente) frenan al contacto con la misma
    razón que el plan. Un contacto importado sin conversación no tiene
    metadata: no hay nada que lo frene salvo su horario.
    """
    to_send: list[RecipientSendPlan] = []
    skipped: list[SkippedRecipient] = []
    for recipient in recipients:
        metadata = metadata_by_session.get(recipient.session_id) or {}
        if received_campaign(metadata, campaign_id):
            skipped.append(SkippedRecipient(recipient.session_id, SKIP_YA_RECIBIDA))
        elif segment_for_metadata(metadata) is None:
            skipped.append(excluded_recipient(recipient.session_id, metadata))
        elif (
            now_ms is not None
            and not recipient.bypass_cooldown
            and has_recent_touch_from_other_campaign(metadata, campaign_id, now_ms)
        ):
            # Otra campaña le llegó mientras esta estaba pausada o en cola.
            skipped.append(SkippedRecipient(recipient.session_id, "campana_reciente"))
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
    #: Salieron pero sin touch (el vault no respondió): sin atribución.
    unstamped: list[str] = field(default_factory=list)
    #: Fallos por tipo de error de Meta (`TemplateMetaError132012`: 98…).
    failure_types: dict[str, int] = field(default_factory=dict)
    #: El mensaje de un fallo, para que el operador sepa qué pasó.
    failure_example: str | None = None
    #: El lote no salió: la mayoría estaba en horario de silencio. El cursor
    #: no avanza y el envío espera hasta esta hora (epoch ms).
    deferred_until_ms: int | None = None


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


#: Cortacircuito: un lote con al menos estos intentos…
BREAKER_MIN_ATTEMPTS = 10
#: …y esta proporción (o más) rechazada por Meta pausa el envío: plantilla
#: pausada, token vencido, sin saldo. Seguir quemaba a toda la audiencia.
BREAKER_FAILURE_SHARE = 0.5


def breaker_reason(batch: CampaignBatchResult) -> str | None:
    """El motivo para pausar sola si Meta rechazó la mayoría del lote; None
    si el lote anduvo (un número inválido suelto no frena nada)."""
    failed = len(batch.failed)
    attempted = batch.sent + failed + len(batch.opted_out)
    if attempted < BREAKER_MIN_ATTEMPTS or failed < attempted * BREAKER_FAILURE_SHARE:
        return None
    kinds = ", ".join(
        f"{kind} ×{count}"
        for kind, count in sorted(batch.failure_types.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    example = f": {batch.failure_example}" if batch.failure_example else ""
    return (
        f"Meta rechazó {failed} de {attempted} envíos del último lote ({kinds}{example}). "
        "Revisa la plantilla, el token o el saldo de WhatsApp y reanuda."
    )
