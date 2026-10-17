"""Cuándo y por dónde se pide la calificación de un pedido entregado (puro).

El flujo durable del pedido (`ReviewRequestWorkflow`) despierta, lee los hechos
(ventana de servicio, horario de silencio, baja de marketing, si ya se pidió) y
le pregunta a `plan_review_request` qué hacer: mandar el formulario dentro de
la ventana, mandar la plantilla, esperar o dejarlo.

Regla del operador (2026-10-13): aprovechar la ventana de 24 h (mensaje normal,
más barato que una plantilla de marketing y sin aprobación de Meta); si está
cerrada, la plantilla con el descuento sale al día siguiente de la entrega.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ReviewAction = Literal["send_flow", "send_template", "wait", "skip"]


@dataclass(frozen=True)
class ReviewTiming:
    """Tiempos de la invitación (configurables por Terraform)."""

    #: Tiempo mínimo tras la entrega para que alcance a abrir y oler la vela.
    min_delay_ms: int
    #: Con la ventana abierta, el formulario sale a más tardar este margen
    #: antes de que cierre.
    window_margin_ms: int
    #: Con la ventana cerrada, la plantilla sale desde este punto tras la entrega.
    template_after_ms: int
    #: Pasado esto ya no se pide (la invitación llegaría fuera de contexto).
    give_up_after_ms: int
    #: Cada cuánto se revisa si el cliente abrió la ventana escribiendo.
    poll_ms: int


@dataclass(frozen=True)
class ReviewRequestFacts:
    """Lo que el flujo leyó del pedido y de la conversación en este despertar."""

    delivered_at_ms: int
    window_expires_at_ms: int | None
    quiet_hours_end_ms: int | None
    opted_out: bool
    order_ok: bool
    already_requested: bool
    already_reviewed: bool
    #: Escalación abierta al equipo, el cliente escribiendo ahora o Meta
    #: Business Agent al frente: la invitación espera (revisión M3).
    conversation_busy: bool = False
    #: Otra invitación del mismo cliente (otro pedido) salió hace poco y no la
    #: ha contestado: esta espera (premortem P10, dos pedidos entregados juntos).
    another_invitation_pending: bool = False


@dataclass(frozen=True)
class ReviewStep:
    action: ReviewAction
    wait_ms: int = 0
    reason: str = ""


def plan_review_request(
    now_ms: int, facts: ReviewRequestFacts, timing: ReviewTiming
) -> ReviewStep:
    """El siguiente paso para la invitación de un pedido.

    Orden: lo que la descarta (pedido no apto, ya pedida/calificada, muy
    vieja) → horario de silencio → ventana abierta (formulario) → ventana
    cerrada (esperar a que escriba o, desde `template_after`, la plantilla).
    La baja de marketing solo frena la plantilla: dentro de la ventana el
    formulario es atención sobre su propio pedido.
    """
    if not facts.order_ok:
        return ReviewStep(action="skip", reason="order_not_eligible")
    if facts.already_reviewed:
        return ReviewStep(action="skip", reason="already_reviewed")
    if facts.already_requested:
        return ReviewStep(action="skip", reason="already_requested")
    age_ms = now_ms - facts.delivered_at_ms
    if age_ms >= timing.give_up_after_ms:
        return ReviewStep(action="skip", reason="expired")
    if facts.quiet_hours_end_ms is not None and facts.quiet_hours_end_ms > now_ms:
        return ReviewStep(
            action="wait", wait_ms=facts.quiet_hours_end_ms - now_ms, reason="quiet_hours"
        )
    if facts.conversation_busy:
        return ReviewStep(action="wait", wait_ms=timing.poll_ms, reason="conversation_busy")
    if facts.another_invitation_pending:
        return ReviewStep(action="wait", wait_ms=timing.poll_ms, reason="another_invitation_pending")

    window_open = (
        facts.window_expires_at_ms is not None and facts.window_expires_at_ms > now_ms
    )
    if window_open:
        assert facts.window_expires_at_ms is not None
        latest_send_ms = facts.window_expires_at_ms - timing.window_margin_ms
        if age_ms >= timing.min_delay_ms or now_ms >= latest_send_ms:
            return ReviewStep(action="send_flow", reason="window_open")
        wake_ms = min(facts.delivered_at_ms + timing.min_delay_ms, latest_send_ms)
        return ReviewStep(action="wait", wait_ms=wake_ms - now_ms, reason="min_delay")

    if age_ms >= timing.template_after_ms:
        if facts.opted_out:
            return ReviewStep(action="skip", reason="opted_out")
        return ReviewStep(action="send_template", reason="window_closed")
    wait_ms = min(timing.poll_ms, timing.template_after_ms - age_ms)
    return ReviewStep(action="wait", wait_ms=wait_ms, reason="waiting_for_window")
