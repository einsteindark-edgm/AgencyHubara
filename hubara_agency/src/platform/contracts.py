"""DTOs cross-cutting de boundary (R-JSON).

Aqui viven los dataclasses planos JSON-serializables que cruzan las
fronteras `workflow.execute_activity` para activities compartidas entre
dominios. Pydantic queda fuera (R-JSON).

ADR-001: las tools no abren `temporal_client` ni hacen `start_workflow`.
Devuelven una **decision** que el workflow lee de `tools_used` (y adjuntos)
y aplica via una activity dedicada (`start_or_signal_sales_workflow_activity`,
`schedule_remarketing_workflow_activity`).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TransferDecision:
    """Decision emitida por `TransferToSalesAgentTool`.

    `target_route` queda como string explicito por compatibilidad con `metadata.json`
    (`active_route`). Las constantes se importan desde `src.core.constants`.
    """
    session_id: str
    target_route: str
    summary: str | None = None


@dataclass
class ScheduleRemarketingDecision:
    """Decision emitida por `ManageConversationTagTool` cuando tag = INTERESADO."""
    session_id: str
    motivo: str
    delay_seconds: int = 60


@dataclass(frozen=True)
class EscalationDecision:
    """Decision emitida por `EscalateToHumanTool`.

    El workflow lee este payload, manda el `final_content` de despedida al
    cliente (si no es vacio), persiste el turno y termina (`_force_shutdown`).
    No programa remarketing — escalation a humano significa que el humano
    toma el control y NO queremos que el bot reactive la conversacion.

    `reason_category` queda como string libre (el enum lo valida la tool en
    su schema JSON, no aca) para no acoplar el dataclass a la taxonomia.
    """
    session_id: str
    reason_category: str
    summary: str


@dataclass(frozen=True)
class EpisodeClosedDecision:
    """Decision emitted by `ManageConversationTagTool` when a CLOSING_TAG
    formally closes an active episode (HU-WA24H-001 Sprint 2).

    The workflow lifts this into a `EpisodeClosedEvent` and dispatches via
    the manifest — the dispatcher routes it to `cancel_watchdog` signal on
    `ServiceWindowWatchdogWorkflow` for the (session_id, episode_id) pair.

    Required because the tool runs inside an activity sandbox (R-DIP forbids
    `temporalio.client` imports from tools), so it can't dispatch directly.
    Same envelope-decision pattern as `ScheduleRemarketingDecision` (ADR-001).

    Fields:
        session_id: the chats session id (`wa_<phone>`).
        episode_id: the episode that just closed (`ep_NNN`).
        closing_tag: the tag that closed it. Carried as the cancellation
            reason for observability (the watchdog logs which tag killed it).
    """

    session_id: str
    episode_id: str
    closing_tag: str


@dataclass(frozen=True)
class OrderRegisteredDecision:
    """Decision emitida por `RegisterOrderTool` cuando `register_order` cierra
    con éxito (orden creada en Medusa, `registered=true`).

    Fix integridad orden↔tag: la secuencia canónica de cierre
    (`register_order` → `manage_conversation_tag(CONFIRMADO_PAGO_PENDIENTE)` →
    `escalate_to_human(PAYMENT_VERIFICATION_PENDING)`) la decide el LLM en
    tool calls separadas, coordinadas SOLO por el prompt. Si el LLM no emite
    el tag/escalación (alucina, agota iteraciones, responde texto directo),
    quedaba una orden huérfana: registrada en Medusa pero sin tag de cierre,
    sin episodio cerrado, sin señal al humano para verificar el pago.

    Esta decisión hace VISIBLE el registro al workflow (antes era invisible —
    el envelope de la tool no llevaba ninguna decisión). El workflow la lee y
    ejecuta `ensure_payment_pending_closure_activity` como RED DE SEGURIDAD
    determinística: garantiza el cierre "pago pendiente" + escalación con la
    durabilidad de Temporal, aunque el LLM no haya completado la secuencia.

    Mismo patrón envelope-decision que `EpisodeClosedDecision` /
    `EscalationDecision` (ADR-001): la tool es inerte respecto a Temporal.

    `motivo` se sintetiza en la tool para que la red de seguridad tenga un
    texto de `status_history` / escalación coherente si tiene que actuar.
    """

    session_id: str
    order_id: str
    payment_method: str
    total_cop: int
    currency: str
    motivo: str


@dataclass(frozen=True)
class PaymentPendingClosureResult:
    """Resultado de `ensure_payment_pending_closure_activity` (red de seguridad).

    La activity es IDEMPOTENTE: si el LLM ya cerró el episodio con
    `CONFIRMADO_PAGO_PENDIENTE` y ya escaló, no toca nada y devuelve
    `episode_closed=None` + `acted=False`. Si el LLM NO completó la secuencia,
    aplica el tag de cierre + escalación y devuelve la `EpisodeClosedDecision`
    del episodio que efectivamente cerró (para que el workflow emita el
    `EpisodeClosedEvent` + CAPI sin duplicar el path del LLM).

    Campos PLANOS para evitar footgun F5 (nested frozen dataclass como return
    type de activity rompe el workflow sandbox). El workflow reconstruye la
    `EpisodeClosedDecision` desde estos campos si `episode_id` viene seteado.
    """

    acted: bool
    escalated: bool
    closed_episode_id: str = ""
    closing_tag: str = ""


@dataclass(frozen=True)
class RemarketingEligibility:
    """Resultado de `check_remarketing_eligibility` activity.

    Post-mortem workflow remarketing-wa_573125671604 run e688685d (2026-05-20):
    el sales workflow escaló al cliente a humano (`active_route=humano`,
    tag=HUMANO via `escalate_to_human` tool). Pero un remarketing previamente
    programado (start_delay=60s) arrancó después de la escalation, hizo
    `claim_conversation_routing(session_id, ROUTE_REMARKETING)` pisando el
    routing, y envió un mensaje al cliente reactivando la conversación —
    lo cual viola la regla de negocio: cuando hay humano en el caso, TODOS
    los bots quedan quietos hasta que el humano devuelva el control.

    Esta dataclass se usa para que el workflow Remarketing chequee el estado
    actual del metadata.json ANTES de tocar nada — si `eligible=False`, el
    workflow returna early sin pisar routing ni enviar mensajes.

    Campos PLANOS (str / bool) para evitar footgun F5 (nested dataclass +
    PEP 563 en activity return type rompe el workflow sandbox).
    """
    eligible: bool
    current_route: str
    current_tag: str
    blocked_reason: str = ""
