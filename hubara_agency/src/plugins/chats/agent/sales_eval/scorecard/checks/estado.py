"""Checks de código de la familia `estado` (HU-SC-1). Ver `scorecard/registry.py`."""
from __future__ import annotations

import re
import unicodedata

from src.plugins.chats.agent.sales_eval.scorecard.checks import code_check
from src.plugins.chats.agent.sales_eval.scorecard.checks._evidence import (
    CONFIRMED_TAGS,
    confirmed_by,
    registered_order_turn,
    tag_turn,
)
from src.plugins.chats.agent.sales_eval.scorecard.checks._helpers import (
    failed,
    in_focus,
    is_legacy,
    judged,
    judged_turns,
    no_signal,
    not_applicable,
    not_judged,
    passed,
    quote,
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
        if in_focus(traj):
            # Sin turno que ponga la etiqueta, la del cierre del episodio es futura.
            return no_signal("TAG-01", "la etiqueta de cierre del episodio no se conoce en el turno foco")
        return not_applicable("TAG-01", "el episodio no quedó en una etiqueta de confirmación")
    t, tag = hit
    if not judged(traj, t):
        return not_judged("TAG-01", traj, f"la etiqueta {tag} se puso en un turno anterior")
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
    for t in judged_turns(traj):
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
    found = [(t, r) for t in judged_turns(traj) for r in _escalations(t) if r in _EVIDENCED_REASONS]
    if not found:
        return not_judged("TAG-02", traj, "sin escalación con motivo que exija evidencia")
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
    if in_focus(traj) and not any(t.turn > human.turn for t in judged_turns(traj)):
        return not_judged("TAG-05", traj, f"la ruta humano empezó en el turno {human.turn}")
    for t in judged_turns(traj):
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
    for t in judged_turns(traj):
        net = bool({"safety_net_closing_escalation", "safety_net_promised_handoff"} & set(t.guards)) or any(
            ch.get("source") == "safety_net" and ch.get("tag") == "HUMANO" for ch in t.state_changes
        )
        if net:
            return failed("TAG-06", t.turn, f"turno {t.turn}: la red de seguridad escaló porque el LLM no lo hizo")
    return passed("TAG-06", "la red de escalación no tuvo que actuar")


# ── TAG-08 · el colega prometido queda avisado (2026-09-30) ──────────────
# El evaluador lee el relevo con su propia regla (no con la del bot que juzga):
# quién (un colega, alguien del equipo…) y qué hará con el cliente.
_HANDOFF_WHO = r"(?:colega|companer[oa]|asesora?|alguien del equipo|persona del equipo|nuestro equipo|el equipo)"
_HANDOFF_PROMISE_RE = re.compile(
    rf"\b{_HANDOFF_WHO}\b[^.!?\n]{{0,60}}?\b(?:te|le|les)\s+(?:van?\s+a\s+)?"
    r"(?:respond|escrib|contact|confirm|coordin|atiend|llam|avis|cuent|ayud)\w*"
    rf"|\b{_HANDOFF_WHO}\b[^.!?\n]{{0,40}}?\b(?:coordin|confirm|habl)\w*\s+contigo"
    r"|\b(?:le|les) paso\s+(?:el aviso|tu caso|tu pedido|tus datos)"
)


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.lower())
    return " ".join("".join(c for c in folded if not unicodedata.combining(c)).split())


@code_check("TAG-08")
def tag_08(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    promised = None
    for t in judged_turns(traj):
        for text in t.sent_texts:
            if not _HANDOFF_PROMISE_RE.search(_plain(text)):
                continue
            if t.state.get("route") != "humano" and not t.tool_ok("escalate_to_human"):
                return failed("TAG-08", t.turn, f"turno {t.turn}: prometió un colega sin escalar {quote(text)}")
            promised = promised or t.turn
    if promised is None:
        return not_judged("TAG-08", traj, "el bot no le prometió un colega al cliente")
    return passed("TAG-08", "el colega prometido quedó avisado", promised)
