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
_HANDOFF_WHO = (
    r"(?:colega|companer[oa]|asesora?|alguien del equipo|persona del equipo|nuestro equipo|el equipo"
    r"|alguien de (?:despachos?|logistica|bodega|produccion|ventas))"
)
_HANDOFF_PROMISE_RE = re.compile(
    rf"\b{_HANDOFF_WHO}\b[^.!?\n]{{0,60}}?\b(?:te|le|les)\s+(?:van?\s+a\s+)?"
    r"(?:respond|escrib|contact|confirm|coordin|atiend|llam|avis|cuent|ayud)\w*"
    rf"|\b{_HANDOFF_WHO}\b[^.!?\n]{{0,40}}?\b(?:coordin|confirm|habl|cuadr|organiz)\w*\b[^.!?\n]{{0,30}}?\bcontigo\b"
    r"|\b(?:le|les) paso\s+(?:el aviso|tu caso|tu pedido|tus datos)"
    rf"|\bpas(?:o|ar|amos|are|aremos)\s+(?:tu|su|tus|sus|el|la)\s+\w+\s+(?:con|a)\s+"
    rf"(?:(?:un|una|el|la|mi|nuestro|nuestra)\s+)?{_HANDOFF_WHO}\b"
)


#: Premortem 2026-10-09: un aviso atado a un evento del pedido («nuestro
#: equipo te avisa cuando despachemos») lo manda el sistema: no es un relevo
#: (misma excepción que la regla del bot, `capabilities/texto.py`).
_ORDER_EVENT_RE = re.compile(
    r"\bcuando\s+(?:lo\s+|te\s+lo\s+|la\s+)?(?:despach\w*|salga\w*|sale\b|envi(?:e|emos)\b"
    r"|este\s+list[oa]|estemos\s+(?:por\s+)?(?:despach|envi)\w*|tengamos\s+la\s+guia|haya\s+novedad\w*"
    r"|(?:tu|el|su)\s+pedido\s+(?:este|salga|llegue|vaya))"
)
_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?\n]?")
#: Coordinar, acordar o cuadrar algo con el cliente es hacerse cargo, aunque
#: la frase nombre un evento del pedido: el aviso puro es la excepción
#: (revisión del premortem, 2026-10-09).
_TAKES_CHARGE_RE = re.compile(r"\b(?:coordin|acord|cuadr|organiz)\w*")


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.lower())
    return " ".join("".join(c for c in folded if not unicodedata.combining(c)).split())


def _promises_handoff(text: str) -> bool:
    for sentence in _SENTENCE_RE.findall(_plain(text)):
        match = _HANDOFF_PROMISE_RE.search(sentence)
        if not match:
            continue
        notice = _ORDER_EVENT_RE.search(sentence[match.start():]) and not _TAKES_CHARGE_RE.search(sentence)
        if not notice:
            return True
    return False


@code_check("TAG-08")
def tag_08(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    promised = None
    for t in judged_turns(traj):
        for text in t.sent_texts:
            if not _promises_handoff(text):
                continue
            if t.state.get("route") != "humano" and not t.tool_ok("escalate_to_human"):
                return failed("TAG-08", t.turn, f"turno {t.turn}: prometió un colega sin escalar {quote(text)}")
            promised = promised or t.turn
    if promised is None:
        return not_judged("TAG-08", traj, "el bot no le prometió un colega al cliente")
    return passed("TAG-08", "el colega prometido quedó avisado", promised)


# ── TAG-09 · lo que promete, lo hace (incidente 2026-10-09) ──────────────
# «Te paso el formulario para los datos de envío» dos turnos seguidos sin el
# formulario. El mismo detector del bot (`use_cases/promised_actions.py`):
# lo prometido sale en ese turno (una tool que lo hizo o la red que lo mandó);
# «tu pedido quedó registrado» exige una orden registrada.


@code_check("TAG-09")
def tag_09(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    from src.plugins.chats.agent.sales.use_cases.promised_actions import promises_by_kind, promised_kinds

    catalog = promises_by_kind()
    promised_at = None
    for t in judged_turns(traj):
        for text in t.sent_texts:
            for kind in sorted(promised_kinds(text)):
                promise = catalog[kind]
                if kind == "registro":
                    kept = registered_order_turn(traj, t.turn) is not None
                else:
                    kept = bool(set(promise.intents) & set(t.intents)) or any(
                        t.tool_ok(name) for name in promise.tools
                    )
                if not kept:
                    return failed("TAG-09", t.turn, f"turno {t.turn}: prometió {kind} y no salió: {quote(text)}")
                promised_at = promised_at or t.turn
    if promised_at is None:
        return not_judged("TAG-09", traj, "el bot no prometió nada para ahora")
    return passed("TAG-09", "lo prometido salió en su turno", promised_at)
