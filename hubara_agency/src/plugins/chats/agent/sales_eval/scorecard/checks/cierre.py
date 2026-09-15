"""Checks de código de la familia `cierre` (HU-SC-1). Ver `scorecard/registry.py`."""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.evals.script_rubric import FORBIDDEN_CLOSINGS
from src.plugins.chats.agent.sales_eval.scorecard.checks import code_check
from src.plugins.chats.agent.sales_eval.scorecard.checks._helpers import (
    failed,
    is_legacy,
    not_applicable,
    passed,
    quote,
    sent_texts,
    unknown,
)
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext, CheckResult
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import Trajectory, Turn

_CPP = "CONFIRMADO_PAGO_PENDIENTE"
_PVP = "PAYMENT_VERIFICATION_PENDING"
_COMPRA_EXITOSA = "COMPRA_EXITOSA"


def _reg_index(traj: Trajectory) -> int | None:
    """Posición del primer turno con register_order exitoso."""
    return next((i for i, t in enumerate(traj.turns) if t.tool_ok("register_order")), None)


def _registration_gate(check_id: str, traj: Trajectory) -> tuple[int | None, CheckResult | None]:
    """(posición del registro, None) o (None, veredicto de aplicabilidad)."""
    idx = _reg_index(traj)
    if idx is not None:
        return idx, None
    unresolved = any(tc.ok is None for t in traj.turns for tc in t.tools_named("register_order"))
    if unresolved or (is_legacy(traj) and traj.order_id):
        return None, unknown(check_id, "sin resultado de register_order (legacy)")
    return None, not_applicable(check_id, "no se registró la orden")


def _ok_before_register(turn: Turn, name: str, *, is_reg_turn: bool) -> bool:
    """La tool fue exitosa en el turno; en el turno del registro, antes de register_order."""
    tools = turn.tools
    if is_reg_turn:
        cut = next(i for i, tc in enumerate(tools) if tc.name == "register_order" and tc.ok is True)
        tools = tools[:cut]
    return any(tc.name == name and tc.ok is True for tc in tools)


@code_check("CIE-01")
def check_canonical_closing_sequence(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    idx, gate = _registration_gate("CIE-01", traj)
    if gate is not None:
        return gate
    reg = traj.turns[idx]
    upto = list(enumerate(traj.turns[: idx + 1]))
    missing = [
        name
        for name in ("verify_order_for_checkout", "present_order_confirmation")
        if not any(_ok_before_register(t, name, is_reg_turn=(i == idx)) for i, t in upto)
    ]
    if missing:
        return failed(
            "CIE-01", reg.turn, f"turno {reg.turn}: register_order sin {' ni '.join(missing)} antes"
        )
    return passed("CIE-01", turn=reg.turn)


def _customer_confirmed(turn: Turn) -> bool:
    return turn.confirm_button or turn.signal == "affirmation"


@code_check("CIE-02")
def check_register_with_confirmation(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    idx, gate = _registration_gate("CIE-02", traj)
    if gate is not None:
        return gate
    reg = traj.turns[idx]
    conf_idx = next(
        (i for i in range(idx, -1, -1) if traj.turns[i].tool_ok("present_order_confirmation")), None
    )
    if conf_idx is not None:
        window = traj.turns[conf_idx + 1 : idx + 1]
        if any(_customer_confirmed(t) for t in window):
            return passed("CIE-02", turn=reg.turn)
        return failed(
            "CIE-02", reg.turn,
            f"turno {reg.turn}: registró sin que el cliente confirmara el resumen del turno {traj.turns[conf_idx].turn}",
        )
    if any(_customer_confirmed(t) or t.confirmed is True for t in traj.turns[: idx + 1]):
        return passed("CIE-02", "confirmación sin resumen previo", turn=reg.turn)
    return failed("CIE-02", reg.turn, f"turno {reg.turn}: registró sin confirmación del cliente")


def _tag_turn(traj: Trajectory, tag: str) -> Turn | None:
    for t in traj.turns:
        by_tool = any(tc.ok is True and tc.args.get("tag") == tag for tc in t.tools_named("manage_conversation_tag"))
        if by_tool or any(c.get("tag") == tag for c in t.state_changes):
            return t
    return None


@code_check("CIE-03")
def check_pending_tag_iff_order(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    tag_turn = _tag_turn(traj, _CPP)
    has_tag = tag_turn is not None or traj.closing_tag == _CPP
    reg_idx = _reg_index(traj)
    has_order = reg_idx is not None or bool(traj.order_id)
    if not has_tag and not has_order:
        return not_applicable("CIE-03", "sin orden ni etiqueta de pago pendiente")
    if has_tag and has_order:
        return passed("CIE-03")
    if is_legacy(traj):
        return unknown("CIE-03", "legacy sin argumentos de etiquetado ni resultado del registro")
    if has_tag:
        turn = tag_turn.turn if tag_turn else None
        where = f"turno {turn}: " if turn else ""
        return failed("CIE-03", turn, f"{where}etiqueta {_CPP} sin orden registrada")
    turn = traj.turns[reg_idx].turn if reg_idx is not None else None
    where = f"turno {turn}: " if turn else ""
    return failed("CIE-03", turn, f"{where}orden registrada sin etiqueta {_CPP}")


@code_check("CIE-03b")
def check_safety_net_closed_order(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    _, gate = _registration_gate("CIE-03b", traj)
    if gate is not None:
        return gate
    if is_legacy(traj):
        return unknown("CIE-03b", "legacy sin guardas")
    for t in traj.turns:
        by_net = any(c.get("source") == "safety_net" and c.get("tag") == _CPP for c in t.state_changes)
        if "safety_net_order_closure" in t.guards or by_net:
            return failed("CIE-03b", t.turn, f"turno {t.turn}: la red de seguridad cerró la orden con {_CPP}")
    return passed("CIE-03b")


@code_check("CIE-04")
def check_payment_verification_escalated(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    idx, gate = _registration_gate("CIE-04", traj)
    if gate is not None:
        return gate
    for t in traj.turns[idx:]:
        by_tool = any(
            tc.ok is True and tc.args.get("reason_category") == _PVP for tc in t.tools_named("escalate_to_human")
        )
        by_change = any(c.get("reason") == _PVP for c in t.state_changes)
        if by_tool or by_change or t.state.get("escalation_reason") == _PVP:
            return passed("CIE-04", turn=t.turn)
    reg = traj.turns[idx]
    return failed("CIE-04", reg.turn, f"turno {reg.turn}: orden registrada sin escalar {_PVP}")


@code_check("CIE-05")
def check_single_closing_message(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    texts = list(sent_texts(traj))
    if not texts:
        return not_applicable("CIE-05", "el bot no envió texto")
    failures: list[tuple[int, str]] = []
    for turn, text in texts:
        if any(rx.search(text) for rx in FORBIDDEN_CLOSINGS):
            failures.append((turn.turn, f"turno {turn.turn}: frase de cierre prohibida {quote(text)}"))
            break
    idx = _reg_index(traj)
    if idx is not None and len(traj.turns[idx].sent_texts) >= 2:
        reg = traj.turns[idx]
        failures.append((reg.turn, f"turno {reg.turn}: {len(reg.sent_texts)} mensajes en el turno del registro"))
    if failures:
        turn, evidence = min(failures, key=lambda f: f[0])
        return failed("CIE-05", turn, evidence)
    return passed("CIE-05")


@code_check("CIE-06")
def check_never_compra_exitosa(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    if is_legacy(traj):
        uses_tag_tool = any(t.tool_attempted("manage_conversation_tag") for t in traj.turns)
        if uses_tag_tool and traj.closing_tag == _COMPRA_EXITOSA:
            return unknown("CIE-06", "legacy sin argumentos: la etiqueta pudo ponerla el humano")
        return passed("CIE-06")
    for t in traj.turns:
        if any(tc.args.get("tag") == _COMPRA_EXITOSA for tc in t.tools_named("manage_conversation_tag")):
            return failed("CIE-06", t.turn, f"turno {t.turn}: el bot intentó etiquetar {_COMPRA_EXITOSA}")
    return passed("CIE-06")


def _amount_problem(turn: Turn) -> str | None:
    for tc in turn.tools:
        if tc.name == "register_order" and tc.error == "amount_mismatch":
            return "register_order reportó amount_mismatch"
        if tc.name == "verify_order_for_checkout" and tc.ok is False:
            return f"verify_order_for_checkout falló ({tc.error or 'sin detalle'})"
        if tc.name == "present_order_confirmation" and tc.error == "price_drift":
            return "present_order_confirmation reportó price_drift"
    return None


@code_check("CIE-07")
def check_amounts_coherent(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    touched = any(
        t.tool_attempted("verify_order_for_checkout") or t.tool_attempted("register_order") for t in traj.turns
    )
    if not touched:
        return not_applicable("CIE-07", "no se verificó ni registró la orden")
    if is_legacy(traj):
        return unknown("CIE-07", "legacy sin resultado de las tools")
    for t in traj.turns:
        if problem := _amount_problem(t):
            return failed("CIE-07", t.turn, f"turno {t.turn}: {problem}")
    return passed("CIE-07")


@code_check("CIE-08")
def check_portavelas_notice(traj: Trajectory, ctx: CheckContext) -> CheckResult:
    _, gate = _registration_gate("CIE-08", traj)
    if gate is not None:
        return gate
    if is_legacy(traj):
        return unknown("CIE-08", "legacy sin guardas")
    for t in traj.turns:
        if "portavelas_notice_guard" in t.guards:
            return failed("CIE-08", t.turn, f"turno {t.turn}: la guarda quitó el aviso de portavelas")
    return passed("CIE-08")
