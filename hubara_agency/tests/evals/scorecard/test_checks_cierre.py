"""Checks de la familia `cierre` (HU-SC-1): secuencia canónica, registro con
confirmación, etiqueta de pago pendiente, escalación de pago, mensaje de
cierre, COMPRA_EXITOSA, montos y aviso de portavelas."""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard.checks import CODE_CHECKS
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from tests.evals.scorecard.dsl import T, tool, traj

_CPP = "CONFIRMADO_PAGO_PENDIENTE"
_PVP = "PAYMENT_VERIFICATION_PENDING"
_CONFIRM = "[el cliente tocó el botón: ✅ Confirmar]"


def _run(check_id: str, t):
    return CODE_CHECKS[check_id](t, CheckContext())


def _closed(**over):
    """Cierre canónico completo: verificar → resumen → confirma → registra."""
    reg_tools = over.pop("reg_tools", [
        tool("register_order"),
        tool("manage_conversation_tag", tag=_CPP),
        tool("escalate_to_human", reason_category=_PVP),
    ])
    return traj(
        T(1, stage_in="cierre", tools=[tool("verify_order_for_checkout")]),
        T(2, stage_in="cierre", tools=[tool("present_order_confirmation")]),
        T(3, stage_in="cierre", inbound=_CONFIRM, signal="affirmation",
          sent=over.pop("reg_sent", ["¡Tu pedido quedó registrado! 🤍"]),
          tools=reg_tools, guards=over.pop("reg_guards", []),
          state=over.pop("reg_state", None)),
        **over,
    )


def _legacy_closed():
    return traj(
        T(1, tools=[tool("verify_order_for_checkout", ok=None)], stage_in=None),
        T(2, tools=[tool("register_order", ok=None)], stage_in=None, sent=["Registrado"]),
        fidelity="legacy",
        order_id="order_01",
    )


# ── CIE-01 ────────────────────────────────────────────────────────────────
def test_cie01_canonical_sequence_passes() -> None:
    r = _run("CIE-01", _closed())
    assert r.check_id == "CIE-01"
    assert r.verdict == "pasa"


def test_cie01_register_without_verification_fails_on_register_turn() -> None:
    t = traj(
        T(1, tools=[tool("present_order_confirmation")]),
        T(2, inbound=_CONFIRM, tools=[tool("register_order")]),
    )
    r = _run("CIE-01", t)
    assert (r.verdict, r.turn) == ("falla", 2)
    assert "verify_order_for_checkout" in r.evidence


def test_cie01_summary_after_register_in_same_turn_fails() -> None:
    t = traj(
        T(1, tools=[tool("verify_order_for_checkout")]),
        T(2, tools=[tool("register_order"), tool("present_order_confirmation")]),
    )
    r = _run("CIE-01", t)
    assert (r.verdict, r.turn) == ("falla", 2)
    assert "present_order_confirmation" in r.evidence


def test_cie01_rejected_register_is_not_applicable() -> None:
    t = traj(T(1, tools=[tool("register_order", ok=False, error="amount_mismatch")]))
    assert _run("CIE-01", t).verdict == "no_aplica"


def test_cie01_legacy_register_is_unknown() -> None:
    assert _run("CIE-01", _legacy_closed()).verdict == "desconocido"


# ── CIE-02 ────────────────────────────────────────────────────────────────
def test_cie02_confirmation_between_summary_and_register_passes() -> None:
    assert _run("CIE-02", _closed()).verdict == "pasa"


def test_cie02_register_in_summary_turn_fails_on_register_turn() -> None:
    t = traj(
        T(1, tools=[tool("verify_order_for_checkout")]),
        T(2, inbound="ok", tools=[tool("present_order_confirmation"), tool("register_order")]),
    )
    r = _run("CIE-02", t)
    assert (r.verdict, r.turn) == ("falla", 2)


def test_cie02_verbal_yes_after_summary_passes() -> None:
    t = traj(
        T(1, tools=[tool("present_order_confirmation")]),
        T(2, inbound="sí, confirmo", signal="affirmation", tools=[tool("register_order")]),
    )
    assert _run("CIE-02", t).verdict == "pasa"


def test_cie02_without_summary_but_confirmed_draft_passes() -> None:
    t = traj(T(1, inbound="sí, de una", confirmed=True, tools=[tool("register_order")]))
    assert _run("CIE-02", t).verdict == "pasa"


def test_cie02_without_summary_nor_confirmation_fails() -> None:
    t = traj(T(1, inbound="hola"), T(2, inbound="ok", tools=[tool("register_order")]))
    assert (_run("CIE-02", t).verdict, _run("CIE-02", t).turn) == ("falla", 2)


def test_cie02_without_register_is_not_applicable() -> None:
    assert _run("CIE-02", traj(T(1, sent=["Hola"]))).verdict == "no_aplica"


def test_cie02_legacy_register_is_unknown() -> None:
    assert _run("CIE-02", _legacy_closed()).verdict == "desconocido"


# ── CIE-03 ────────────────────────────────────────────────────────────────
def test_cie03_order_and_pending_tag_together_pass() -> None:
    assert _run("CIE-03", _closed()).verdict == "pasa"


def test_cie03_pending_tag_without_order_fails_on_tag_turn() -> None:
    t = traj(T(1, sent=["Hola"]), T(2, tools=[tool("manage_conversation_tag", tag=_CPP)]))
    r = _run("CIE-03", t)
    assert (r.verdict, r.turn) == ("falla", 2)


def test_cie03_order_without_pending_tag_fails_on_register_turn() -> None:
    t = _closed(reg_tools=[tool("register_order"), tool("escalate_to_human", reason_category=_PVP)])
    r = _run("CIE-03", t)
    assert (r.verdict, r.turn) == ("falla", 3)


def test_cie03_tag_from_state_change_counts() -> None:
    t = _closed(
        reg_tools=[tool("register_order")],
        reg_state={"tag": _CPP, "route": "ventas", "changes": [{"tag": _CPP, "source": "llm", "reason": None}]},
    )
    assert _run("CIE-03", t).verdict == "pasa"


def test_cie03_without_order_nor_tag_is_not_applicable() -> None:
    assert _run("CIE-03", traj(T(1, sent=["Hola"]), closing_tag="INTERESADO")).verdict == "no_aplica"


# ── CIE-03b ───────────────────────────────────────────────────────────────
def test_cie03b_llm_closed_order_passes() -> None:
    assert _run("CIE-03b", _closed()).verdict == "pasa"


def test_cie03b_safety_net_guard_fails_on_that_turn() -> None:
    r = _run("CIE-03b", _closed(reg_guards=["safety_net_order_closure"]))
    assert (r.verdict, r.turn) == ("falla", 3)


def test_cie03b_safety_net_state_change_fails() -> None:
    t = _closed(
        reg_tools=[tool("register_order")],
        reg_state={"tag": _CPP, "route": "ventas", "changes": [{"tag": _CPP, "source": "safety_net", "reason": None}]},
    )
    assert (_run("CIE-03b", t).verdict, _run("CIE-03b", t).turn) == ("falla", 3)


def test_cie03b_without_register_is_not_applicable() -> None:
    assert _run("CIE-03b", traj(T(1, sent=["Hola"]))).verdict == "no_aplica"


def test_cie03b_legacy_is_unknown() -> None:
    assert _run("CIE-03b", _legacy_closed()).verdict == "desconocido"


# ── CIE-04 ────────────────────────────────────────────────────────────────
def test_cie04_payment_verification_escalated_passes() -> None:
    assert _run("CIE-04", _closed()).verdict == "pasa"


def test_cie04_escalation_reason_in_state_passes() -> None:
    t = _closed(reg_tools=[tool("register_order")],
                reg_state={"tag": "HUMANO", "route": "humano", "escalation_reason": _PVP, "changes": []})
    assert _run("CIE-04", t).verdict == "pasa"


def test_cie04_register_without_escalation_fails_on_register_turn() -> None:
    t = _closed(reg_tools=[tool("register_order"), tool("manage_conversation_tag", tag=_CPP)])
    r = _run("CIE-04", t)
    assert (r.verdict, r.turn) == ("falla", 3)
    assert _PVP in r.evidence


def test_cie04_wrong_escalation_reason_fails() -> None:
    t = _closed(reg_tools=[tool("register_order"), tool("escalate_to_human", reason_category="OTHER")])
    assert _run("CIE-04", t).verdict == "falla"


def test_cie04_without_register_is_not_applicable() -> None:
    assert _run("CIE-04", traj(T(1, sent=["Hola"]))).verdict == "no_aplica"


# ── CIE-05 ────────────────────────────────────────────────────────────────
def test_cie05_single_closing_message_passes() -> None:
    assert _run("CIE-05", _closed()).verdict == "pasa"


def test_cie05_forbidden_closing_phrase_fails_on_that_turn() -> None:
    t = traj(T(1, sent=["Claro"]), T(2, sent=["¡Gracias por tu compra!"]))
    r = _run("CIE-05", t)
    assert (r.verdict, r.turn) == ("falla", 2)
    assert "Gracias por tu compra" in r.evidence


def test_cie05_two_messages_on_register_turn_fails() -> None:
    r = _run("CIE-05", _closed(reg_sent=["¡Tu pedido quedó registrado!", "En breve verificamos el pago"]))
    assert (r.verdict, r.turn) == ("falla", 3)


def test_cie05_without_text_is_not_applicable() -> None:
    assert _run("CIE-05", traj(T(1, tools=[tool("present_products")]))).verdict == "no_aplica"


# ── CIE-06 ────────────────────────────────────────────────────────────────
def test_cie06_other_tags_pass() -> None:
    t = traj(T(1, tools=[tool("manage_conversation_tag", tag="INTERESADO")]))
    assert _run("CIE-06", t).verdict == "pasa"


def test_cie06_bot_attempts_compra_exitosa_fails_on_that_turn() -> None:
    t = traj(T(1, sent=["Hola"]), T(2, tools=[tool("manage_conversation_tag", ok=False, error="forbidden", tag="COMPRA_EXITOSA")]))
    r = _run("CIE-06", t)
    assert (r.verdict, r.turn) == ("falla", 2)


def test_cie06_legacy_tag_tool_with_compra_exitosa_closing_is_unknown() -> None:
    t = traj(T(1, tools=[tool("manage_conversation_tag", ok=None)]), fidelity="legacy", closing_tag="COMPRA_EXITOSA")
    assert _run("CIE-06", t).verdict == "desconocido"


def test_cie06_legacy_without_compra_exitosa_passes() -> None:
    t = traj(T(1, tools=[tool("manage_conversation_tag", ok=None)]), fidelity="legacy", closing_tag="INTERESADO")
    assert _run("CIE-06", t).verdict == "pasa"


# ── CIE-07 ────────────────────────────────────────────────────────────────
def test_cie07_coherent_amounts_pass() -> None:
    assert _run("CIE-07", _closed()).verdict == "pasa"


def test_cie07_register_amount_mismatch_fails_on_that_turn() -> None:
    t = traj(
        T(1, tools=[tool("verify_order_for_checkout")]),
        T(2, tools=[tool("register_order", ok=False, error="amount_mismatch")]),
    )
    r = _run("CIE-07", t)
    assert (r.verdict, r.turn) == ("falla", 2)
    assert "amount_mismatch" in r.evidence


def test_cie07_failed_verification_fails() -> None:
    t = traj(T(1, tools=[tool("verify_order_for_checkout", ok=False, error="not_verified")]))
    assert (_run("CIE-07", t).verdict, _run("CIE-07", t).turn) == ("falla", 1)


def test_cie07_price_drift_on_summary_fails() -> None:
    t = traj(
        T(1, tools=[tool("verify_order_for_checkout")]),
        T(2, tools=[tool("present_order_confirmation", ok=False, error="price_drift")]),
    )
    assert (_run("CIE-07", t).verdict, _run("CIE-07", t).turn) == ("falla", 2)


def test_cie07_price_mismatch_on_summary_fails() -> None:
    """Run ebbc203d: el LLM pasó el precio del anuncio; la tool lo rechaza."""
    t = traj(
        T(1, tools=[tool("verify_order_for_checkout")]),
        T(2, tools=[tool("present_order_confirmation", ok=False, error="price_mismatch")]),
    )
    r = _run("CIE-07", t)
    assert (r.verdict, r.turn) == ("falla", 2)
    assert "price_mismatch" in r.evidence


def test_cie07_shipping_mismatch_fails_on_that_turn() -> None:
    """Decisión del operador 2026-09-23: el envío es siempre una tarifa
    publicada; un envío inventado (o $0) es un monto incoherente."""
    for name in ("present_order_confirmation", "register_order"):
        t = traj(
            T(1, tools=[tool("verify_order_for_checkout")]),
            T(2, tools=[tool(name, ok=False, error="shipping_mismatch")]),
        )
        r = _run("CIE-07", t)
        assert (r.verdict, r.turn) == ("falla", 2), name
        assert "shipping_mismatch" in r.evidence


def test_cie07_without_verify_nor_register_is_not_applicable() -> None:
    assert _run("CIE-07", traj(T(1, sent=["Hola"]))).verdict == "no_aplica"


def test_cie07_legacy_is_unknown() -> None:
    assert _run("CIE-07", _legacy_closed()).verdict == "desconocido"


# ── CIE-08 ────────────────────────────────────────────────────────────────
def test_cie08_without_portavelas_guard_passes() -> None:
    assert _run("CIE-08", _closed()).verdict == "pasa"


def test_cie08_portavelas_guard_fails_on_that_turn() -> None:
    r = _run("CIE-08", _closed(reg_guards=["portavelas_notice_guard"]))
    assert (r.verdict, r.turn) == ("falla", 3)


def test_cie08_without_register_is_not_applicable() -> None:
    assert _run("CIE-08", traj(T(1, guards=["portavelas_notice_guard"]))).verdict == "no_aplica"


def test_cie08_legacy_is_unknown() -> None:
    assert _run("CIE-08", _legacy_closed()).verdict == "desconocido"
