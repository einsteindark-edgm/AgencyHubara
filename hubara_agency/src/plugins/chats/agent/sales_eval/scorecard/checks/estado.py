"""Checks de código de la familia `estado` (HU-SC-1). Ver `scorecard/registry.py`."""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard.checks import code_check
from src.plugins.chats.agent.sales_eval.scorecard.checks._evidence import (
    CONFIRMED_TAGS,
    confirmed_by,
    registered_order_turn,
    tag_turn,
)
from src.plugins.chats.agent.sales_eval.scorecard.checks._helpers import (
    failed,
    is_legacy,
    not_applicable,
    passed,
    unknown,
)
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext, CheckResult
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import Trajectory, Turn

_EVIDENCED_REASONS = ("ORDER_PENDING_SHIPPING_DETAILS", "PAYMENT_VERIFICATION_PENDING")
_RECEIPT_MARKERS = ("[el cliente envió un documento pdf", "comprobante")


@code_check("TAG-01")
def tag_01(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    hit = tag_turn(traj, CONFIRMED_TAGS)
    if hit is None:
        return not_applicable("TAG-01", "el episodio no quedó en una etiqueta de confirmación")
    t, tag = hit
    if tag == "CONFIRMADO_PAGO_PENDIENTE" and (
        registered_order_turn(traj, t.turn) is not None or traj.order_id
    ):
        return passed("TAG-01", "orden registrada sostiene el pago pendiente", t.turn)
    backing = confirmed_by(traj, t.turn)
    if backing is None:
        return failed(
            "TAG-01",
            t.turn,
            f"turno {t.turn}: etiqueta {tag} sin confirmación del cliente en el episodio",
        )
    return passed("TAG-01", f"confirmación del cliente en el turno {backing.turn}", t.turn)


@code_check("TAG-01b")
def tag_01b(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    if is_legacy(traj):
        return unknown("TAG-01b", "sin trazas: no se ven las degradaciones")
    for t in traj.turns:
        for call in t.tools_named("manage_conversation_tag"):
            degraded = call.note("degraded_from")
            if degraded:
                return failed(
                    "TAG-01b",
                    t.turn,
                    f"turno {t.turn}: el LLM etiquetó {degraded} y la guarda lo degradó por falta de confirmación",
                )
    return passed("TAG-01b", "ninguna etiqueta fue degradada")


def _escalations(t: Turn) -> list[str]:
    reasons = [
        str(c.args.get("reason_category"))
        for c in t.tools_named("escalate_to_human")
        if c.ok is True and c.args.get("reason_category")
    ]
    reasons += [str(ch["reason"]) for ch in t.state_changes if ch.get("reason")]
    return reasons


@code_check("TAG-02")
def tag_02(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    if is_legacy(traj):
        if any(t.tool_attempted("escalate_to_human") for t in traj.turns):
            return unknown("TAG-02", "sin trazas: no se ve el motivo de la escalación")
        return not_applicable("TAG-02", "sin escalación registrada")
    found = [(t, r) for t in traj.turns for r in _escalations(t) if r in _EVIDENCED_REASONS]
    if not found:
        return not_applicable("TAG-02", "sin escalación con motivo que exija evidencia")
    for t, reason in found:
        if reason == "ORDER_PENDING_SHIPPING_DETAILS" and confirmed_by(traj, t.turn) is None:
            return failed(
                "TAG-02", t.turn,
                f"turno {t.turn}: escalación {reason} sin confirmación de compra del cliente",
            )
        if reason == "PAYMENT_VERIFICATION_PENDING":
            receipt = any(
                any(m in x.inbound_text.lower() for m in _RECEIPT_MARKERS)
                for x in traj.turns if x.turn <= t.turn
            )
            if registered_order_turn(traj, t.turn) is None and not traj.order_id and not receipt:
                return failed(
                    "TAG-02", t.turn,
                    f"turno {t.turn}: escalación {reason} sin orden registrada ni comprobante",
                )
    return passed("TAG-02", "motivos de escalación sostenidos")


@code_check("TAG-05")
def tag_05(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    if is_legacy(traj):
        return not_applicable("TAG-05", "la trayectoria legada corta en el humano")
    human = next((t for t in traj.turns if t.state.get("route") == "humano"), None)
    if human is None:
        return not_applicable("TAG-05", "la sesión no pasó a ruta humano")
    for t in traj.turns:
        if t.turn > human.turn and (t.sent_texts or t.intents):
            return failed(
                "TAG-05", t.turn,
                f"turno {t.turn}: el bot le habló al cliente con la sesión en ruta humano (desde el turno {human.turn})",
            )
    return passed("TAG-05", f"silencio tras la ruta humano del turno {human.turn}", human.turn)


@code_check("TAG-06")
def tag_06(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    if is_legacy(traj):
        return unknown("TAG-06", "sin trazas: no se ven las redes de seguridad")
    for t in traj.turns:
        net = "safety_net_closing_escalation" in t.guards or any(
            ch.get("source") == "safety_net" and ch.get("tag") == "HUMANO" for ch in t.state_changes
        )
        if net:
            return failed("TAG-06", t.turn, f"turno {t.turn}: la red de seguridad escaló porque el LLM no lo hizo")
    return passed("TAG-06", "la red de escalación no tuvo que actuar")
