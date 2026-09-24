"""Checks de código de la familia `confirmacion` (HU-SC-1). Ver `scorecard/registry.py`.

El corazón del PR #281: el bot mandó el formulario de envío cuando el
cliente había dicho "voy apenas en camino a casa".
"""
from __future__ import annotations

import re

from src.plugins.chats.agent.sales_eval.scorecard.checks import code_check
from src.plugins.chats.agent.sales_eval.scorecard.checks._evidence import (
    CONFIRMED_TAGS,
    confirmed_by,
    registered_order_turn,
)
from src.plugins.chats.agent.sales_eval.scorecard.checks._helpers import (
    failed,
    is_legacy,
    judged_turns,
    not_judged,
    passed,
    quote,
    sent_texts,
    unknown,
)
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext, CheckResult
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import Trajectory, Turn

_ADVANCE_TOOLS = ("request_shipping_details", "present_order_confirmation", "register_order")
_ADVANCE_INTENTS = ("shipping_flow", "order_confirmation")
_GUARD_ERRORS = ("purchase_not_confirmed", "customer_deferred")
_CLAIM_RE = re.compile(
    r"\b(pedido|compra|orden)\b[^.?!\n]{0,40}\b(qued[oó]|est[aá]|fue|ya est[aá])\s+"
    r"(confirmad[oa]|registrad[oa])\b",
    re.IGNORECASE,
)


def _last_signal_text(traj: Trajectory, upto: int) -> str:
    for t in reversed(traj.turns):
        if t.turn <= upto and t.signal:
            label = "aplazamiento" if t.signal == "deferral" else "afirmación"
            return f"última señal del cliente: {label} {quote(t.inbound_text, 80)}"
    return "el cliente no dijo que sí ni tocó Confirmar"


@code_check("CON-01")
def con_01(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    forms = [t for t in judged_turns(traj) if "shipping_flow" in t.intents]
    if not forms:
        return not_judged("CON-01", traj, "no se envió el formulario de envío")
    for form in forms:
        if confirmed_by(traj, form.turn) is None:
            return failed(
                "CON-01",
                form.turn,
                f"turno {form.turn}: formulario de envío sin confirmación de compra; "
                + _last_signal_text(traj, form.turn),
            )
    backing = confirmed_by(traj, forms[0].turn)
    return passed("CON-01", f"confirmación en el turno {backing.turn if backing else '?'}", forms[0].turn)


def _advanced(t: Turn, legacy: bool) -> str | None:
    for name in _ADVANCE_TOOLS:
        if t.tool_ok(name):
            return name
    for call in t.tools_named("manage_conversation_tag"):
        if call.ok is True and call.args.get("tag") in CONFIRMED_TAGS and call.note("degraded_from") is None:
            return f"manage_conversation_tag({call.args['tag']})"
    if legacy:
        hit = next((i for i in t.intents if i in _ADVANCE_INTENTS), None)
        return hit
    return None


@code_check("CON-02")
def con_02(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    deferrals = [t for t in judged_turns(traj) if t.signal == "deferral"]
    if not deferrals:
        return not_judged("CON-02", traj, "el cliente no aplazó")
    legacy = is_legacy(traj)
    for t in deferrals:
        advance = _advanced(t, legacy)
        if advance:
            return failed(
                "CON-02",
                t.turn,
                f"turno {t.turn}: el cliente aplazó {quote(t.inbound_text, 80)} y el bot avanzó con {advance}",
            )
    return passed("CON-02", f"{len(deferrals)} aplazamiento(s) respetado(s)")


@code_check("CON-03")
def con_03(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    claims = [(t, text) for t, text in sent_texts(traj) if _CLAIM_RE.search(text)]
    if not claims:
        return not_judged("CON-03", traj, "el bot no afirmó un pedido confirmado o registrado")
    legacy = is_legacy(traj)
    for t, text in claims:
        has_order = registered_order_turn(traj, t.turn) is not None or (
            legacy and any(x.tool_attempted("register_order") for x in traj.turns if x.turn <= t.turn)
        )
        if not has_order:
            return failed("CON-03", t.turn, f"turno {t.turn}: {quote(text)} sin orden registrada")
    return passed("CON-03", "las afirmaciones de registro tienen orden")


@code_check("CON-05")
def con_05(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    if is_legacy(traj):
        return unknown("CON-05", "sin trazas: no se ven los rechazos de las guardas")
    for t in judged_turns(traj):
        for call in t.tools:
            if call.error in _GUARD_ERRORS:
                return failed(
                    "CON-05",
                    t.turn,
                    f"turno {t.turn}: {call.name} rechazada por {call.error} (el LLM intentó avanzar)",
                )
    return passed("CON-05", "ninguna guarda de confirmación tuvo que actuar")
