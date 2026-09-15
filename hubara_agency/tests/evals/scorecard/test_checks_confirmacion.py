"""Checks de confirmación de compra (CON-*) — el corazón del PR #281."""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard.checks import CODE_CHECKS
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from tests.evals.scorecard.dsl import T, tool, traj

CTX = CheckContext()


def run(cid, t):
    return CODE_CHECKS[cid](t, CTX)


# CON-01 ─────────────────────────────────────────────────────────────────────
def test_con01_form_after_explicit_yes_passes() -> None:
    t = traj(T(1, inbound="sí, lo quiero", signal="affirmation", confirmed=True),
             T(2, tools=[tool("request_shipping_details")]))
    assert run("CON-01", t).verdict == "pasa"


def test_con01_form_after_confirm_button_passes() -> None:
    t = traj(T(1, inbound="[el cliente tocó el botón: ✅ Confirmar]", tools=[tool("request_shipping_details")]))
    assert run("CON-01", t).verdict == "pasa"


def test_con01_form_without_confirmation_fails_on_form_turn() -> None:
    t = traj(T(1, inbound="el primero azulito"), T(2, inbound="ok", tools=[tool("request_shipping_details")]))
    r = run("CON-01", t)
    assert (r.verdict, r.turn) == ("falla", 2)


def test_con01_later_deferral_cancels_an_earlier_yes() -> None:
    t = traj(T(1, inbound="sí", signal="affirmation"),
             T(2, inbound="mejor luego", signal="deferral", tools=[tool("request_shipping_details")]))
    r = run("CON-01", t)
    assert (r.verdict, r.turn) == ("falla", 2)


def test_con01_rejected_form_is_not_applicable() -> None:
    t = traj(T(1, tools=[tool("request_shipping_details", ok=False, error="purchase_not_confirmed")]))
    assert run("CON-01", t).verdict == "no_aplica"


# CON-02 ─────────────────────────────────────────────────────────────────────
def test_con02_deferral_with_advance_tool_fails() -> None:
    t = traj(T(1, inbound="voy en camino a casa", signal="deferral", tools=[tool("request_shipping_details")]))
    r = run("CON-02", t)
    assert (r.verdict, r.turn) == ("falla", 1)


def test_con02_deferral_answered_with_text_passes() -> None:
    t = traj(T(1, inbound="luego te escribo", signal="deferral", sent=["Claro, aquí te espero 🤍"]))
    assert run("CON-02", t).verdict == "pasa"


def test_con02_confirmed_tag_on_deferral_turn_fails() -> None:
    t = traj(T(1, signal="deferral", tools=[tool("manage_conversation_tag", tag="CONFIRMADO_SIN_DATOS")]))
    assert run("CON-02", t).verdict == "falla"


def test_con02_without_deferral_is_not_applicable_and_legacy_uses_intents() -> None:
    assert run("CON-02", traj(T(1))).verdict == "no_aplica"
    legacy = traj(T(1, signal="deferral", tools=[tool("request_shipping_details", ok=None)], intents=["shipping_flow"]),
                  fidelity="legacy")
    assert run("CON-02", legacy).verdict == "falla"


# CON-03 ─────────────────────────────────────────────────────────────────────
def test_con03_claims_registered_without_order_fails() -> None:
    t = traj(T(1, sent=["Listo, tu pedido quedó registrado 🤍"]))
    r = run("CON-03", t)
    assert (r.verdict, r.turn) == ("falla", 1)


def test_con03_claim_after_register_order_passes_and_no_claim_is_na() -> None:
    ok = traj(T(1, tools=[tool("register_order")], sent=["Listo, tu pedido quedó registrado 🤍"]))
    assert run("CON-03", ok).verdict == "pasa"
    assert run("CON-03", traj(T(1, sent=["¿Te gusta este?"]))).verdict == "no_aplica"


# CON-05 ─────────────────────────────────────────────────────────────────────
def test_con05_guard_rejection_fails_as_llm_attempt() -> None:
    t = traj(T(1), T(2, tools=[tool("request_shipping_details", ok=False, error="customer_deferred")]))
    r = run("CON-05", t)
    assert (r.verdict, r.turn) == ("falla", 2)


def test_con05_passes_without_rejection_and_is_unknown_in_legacy() -> None:
    assert run("CON-05", traj(T(1))).verdict == "pasa"
    assert run("CON-05", traj(T(1), fidelity="legacy")).verdict == "desconocido"
