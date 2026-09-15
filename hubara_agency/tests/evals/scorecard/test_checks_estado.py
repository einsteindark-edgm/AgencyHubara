"""Checks de etiquetado y escalación (TAG-*)."""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard.checks import CODE_CHECKS
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from tests.evals.scorecard.dsl import T, tool, traj

CTX = CheckContext()


def run(cid, t):
    return CODE_CHECKS[cid](t, CTX)


def _tag_state(tag, source="llm", reason=None, **extra):
    return {"tag": tag, "route": extra.pop("route", "ventas"), **extra,
            "changes": [{"tag": tag, "source": source, "reason": reason}]}


# TAG-01 ─────────────────────────────────────────────────────────────────────
def test_tag01_confirmed_without_customer_yes_fails_on_tag_turn() -> None:
    t = traj(T(1, inbound="voy en camino", signal="deferral"),
             T(2, trigger="ghost", tools=[tool("manage_conversation_tag", tag="CONFIRMADO_SIN_DATOS")],
               state=_tag_state("CONFIRMADO_SIN_DATOS")),
             closing_tag="CONFIRMADO_SIN_DATOS")
    r = run("TAG-01", t)
    assert (r.verdict, r.turn) == ("falla", 2)


def test_tag01_confirmed_with_yes_passes() -> None:
    t = traj(T(1, inbound="sí, confirmo", signal="affirmation", confirmed=True),
             T(2, trigger="ghost", state=_tag_state("CONFIRMADO_SIN_DATOS")),
             closing_tag="CONFIRMADO_SIN_DATOS")
    assert run("TAG-01", t).verdict == "pasa"


def test_tag01_payment_pending_backed_by_registered_order_passes() -> None:
    t = traj(T(1, tools=[tool("register_order")], state=_tag_state("CONFIRMADO_PAGO_PENDIENTE")),
             closing_tag="CONFIRMADO_PAGO_PENDIENTE")
    assert run("TAG-01", t).verdict == "pasa"


def test_tag01_degraded_tag_or_other_closing_is_not_applicable() -> None:
    degraded = traj(T(1, tools=[tool("manage_conversation_tag", notes=["degraded_from:CONFIRMADO_SIN_DATOS"],
                                     tag="CONFIRMADO_SIN_DATOS")], state=_tag_state("INTERESADO")))
    assert run("TAG-01", degraded).verdict == "no_aplica"
    assert run("TAG-01", traj(T(1), closing_tag="RECHAZO")).verdict == "no_aplica"


# TAG-01b ────────────────────────────────────────────────────────────────────
def test_tag01b_degradation_fails_as_llm_attempt() -> None:
    t = traj(T(1), T(2, tools=[tool("manage_conversation_tag", notes=["degraded_from:CONFIRMADO_SIN_DATOS"])]))
    r = run("TAG-01b", t)
    assert (r.verdict, r.turn) == ("falla", 2)
    assert run("TAG-01b", traj(T(1))).verdict == "pasa"


# TAG-02 ─────────────────────────────────────────────────────────────────────
def test_tag02_pending_shipping_escalation_without_confirmation_fails() -> None:
    t = traj(T(1), T(2, tools=[tool("escalate_to_human", reason_category="ORDER_PENDING_SHIPPING_DETAILS")]))
    r = run("TAG-02", t)
    assert (r.verdict, r.turn) == ("falla", 2)


def test_tag02_payment_verification_after_order_or_receipt_passes() -> None:
    after_order = traj(T(1, tools=[tool("register_order"),
                                   tool("escalate_to_human", reason_category="PAYMENT_VERIFICATION_PENDING")]))
    assert run("TAG-02", after_order).verdict == "pasa"
    receipt = traj(T(1, inbound="[el cliente envió un documento PDF: comprobante.pdf]",
                     state=_tag_state("HUMANO", reason="PAYMENT_VERIFICATION_PENDING", route="humano")))
    assert run("TAG-02", receipt).verdict == "pasa"


def test_tag02_payment_verification_without_order_nor_receipt_fails() -> None:
    t = traj(T(1, tools=[tool("escalate_to_human", reason_category="PAYMENT_VERIFICATION_PENDING")]))
    assert run("TAG-02", t).verdict == "falla"


def test_tag02_other_reasons_are_not_applicable() -> None:
    t = traj(T(1, tools=[tool("escalate_to_human", reason_category="DISCOUNT_REQUEST")]))
    assert run("TAG-02", t).verdict == "no_aplica"


# TAG-05 ─────────────────────────────────────────────────────────────────────
def test_tag05_bot_speaking_after_human_route_fails() -> None:
    t = traj(T(1, state={"tag": "HUMANO", "route": "humano", "changes": []}),
             T(2, sent=["¿Algo más?"]))
    r = run("TAG-05", t)
    assert (r.verdict, r.turn) == ("falla", 2)


def test_tag05_silent_after_human_route_passes_and_never_human_is_na() -> None:
    t = traj(T(1, state={"tag": "HUMANO", "route": "humano", "changes": []}))
    assert run("TAG-05", t).verdict == "pasa"
    assert run("TAG-05", traj(T(1))).verdict == "no_aplica"


# TAG-06 ─────────────────────────────────────────────────────────────────────
def test_tag06_safety_net_escalation_fails() -> None:
    t = traj(T(1, guards=["safety_net_closing_escalation"]))
    assert (run("TAG-06", t).verdict, run("TAG-06", t).turn) == ("falla", 1)
    assert run("TAG-06", traj(T(1))).verdict == "pasa"
    assert run("TAG-06", traj(T(1), fidelity="legacy")).verdict == "desconocido"
