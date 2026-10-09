"""Checks de código de la familia `confirmacion` (HU-SC-1). Ver `scorecard/registry.py`.

El corazón del PR #281: el bot mandó el formulario de envío cuando el
cliente había dicho "voy apenas en camino a casa".
"""
from __future__ import annotations

import re

from src.plugins.chats.agent.sales_eval.scorecard.checks import code_check
from src.plugins.chats.agent.sales_eval.scorecard.checks._evidence import (
    CONFIRMED_TAGS,
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


def _product_chosen(traj: Trajectory, upto: int) -> bool:
    """El borrador del pedido tenía un producto en algún turno hasta `upto`
    (el borrador de la traza es el de después del turno)."""
    for t in traj.turns:
        if t.turn > upto:
            break
        draft = t.draft or {}
        items = draft.get("items")
        if str(draft.get("producto") or "").strip() or (
            isinstance(items, list) and any(isinstance(i, dict) and i.get("producto") for i in items)
        ):
            return True
    return False


def _deferral_in_force(traj: Trajectory, upto: int) -> Turn | None:
    """El aplazamiento que sigue vigente en `upto`: la última señal del cliente
    hasta ese turno fue un «después» (un sí o el botón posteriores lo anulan)."""
    last: Turn | None = None
    for t in traj.turns:
        if t.turn > upto:
            break
        if t.signal or t.confirm_button:
            last = t
    return last if last is not None and last.signal == "deferral" else None


@code_check("CON-01")
def con_01(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    """v10 (incidente del 2026-10-09, criterio del operador): el formulario no
    espera un «sí». Sale con el producto elegido y sin un aplazamiento
    vigente (el corazón del PR #281: «voy apenas en camino a casa»)."""
    forms = [t for t in judged_turns(traj) if "shipping_flow" in t.intents]
    if not forms:
        return not_judged("CON-01", traj, "no se envió el formulario de envío")
    for form in forms:
        deferral = _deferral_in_force(traj, form.turn)
        if deferral is not None:
            return failed(
                "CON-01",
                form.turn,
                f"turno {form.turn}: formulario de envío con el cliente aplazando "
                f"{quote(deferral.inbound_text, 80)}",
            )
        if not _product_chosen(traj, form.turn):
            return failed("CON-01", form.turn, f"turno {form.turn}: formulario de envío sin producto elegido")
    return passed("CON-01", "formulario con producto elegido y sin aplazamiento", forms[0].turn)


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
