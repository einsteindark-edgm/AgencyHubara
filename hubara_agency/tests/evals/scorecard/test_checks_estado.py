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


# TAG-08 · el colega prometido queda avisado ─────────────────────────────────
# Laboratorio caso-cortesia-1001 (2026-09-30): los dos bots dijeron «un colega
# del equipo coordina contigo la entrega» sin escalar; nadie quedaba avisado.

PROMISE = "Claro que sí 🤍 Un colega del equipo coordina contigo la entrega de hoy después de las 5."


def test_tag08_a_promise_without_escalation_fails() -> None:
    t = traj(T(1, sent=[PROMISE], state=_tag_state("NO_ETIQUETADO")))

    r = run("TAG-08", t)

    assert (r.verdict, r.turn) == ("falla", 1)
    assert "coordina contigo" in r.evidence


def test_tag08_reads_the_phrasings_of_the_new_bot() -> None:
    """Laboratorio r3: lo que se coordina va entre el verbo y «contigo», y el
    caso «se pasa» a un colega."""
    for text in (
        "Claro que sí, un colega del equipo coordina la entrega contigo en este mismo chat 🤍",
        "Déjame pasar tu caso con un colega del equipo para que confirme la logística contigo.",
    ):
        t = traj(T(1, sent=[text], state=_tag_state("NO_ETIQUETADO")))

        assert run("TAG-08", t).verdict == "falla", text


def test_tag08_a_promise_with_the_llm_escalation_passes() -> None:
    t = traj(T(1, sent=["Un colega del equipo te responde en este mismo chat 🤍"],
               tools=[tool("escalate_to_human", reason_category="SHIPPING_ISSUE")],
               state=_tag_state("HUMANO", route="humano")))

    assert run("TAG-08", t).verdict == "pasa"


def test_tag08_the_safety_net_keeps_the_promise_but_tag06_records_it() -> None:
    t = traj(T(1, sent=[PROMISE], guards=["safety_net_promised_handoff"],
               state=_tag_state("HUMANO", source="safety_net", route="humano")))

    assert run("TAG-08", t).verdict == "pasa"
    assert run("TAG-06", t).verdict == "falla"


def test_tag08_a_reply_without_a_promise_is_not_judged() -> None:
    t = traj(T(1, sent=["Qué alegría que ya lo tengas contigo 🤍 Cualquier cosa que necesites, aquí estamos."]))

    assert run("TAG-08", t).verdict == "no_aplica"


def test_tag08_an_order_event_notice_is_not_a_promise() -> None:
    """Premortem 2026-10-09: «nuestro equipo te avisa cuando despachemos» es el
    aviso que manda el sistema al despachar, no un colega que se hace cargo."""
    t = traj(T(1, sent=["Listo 🤍 Nuestro equipo te avisa cuando despachemos tu pedido."],
               state=_tag_state("NO_ETIQUETADO")))

    assert run("TAG-08", t).verdict == "no_aplica"


def test_tag08_is_a_major_state_check() -> None:
    from src.plugins.chats.agent.sales_eval.scorecard.registry import REGISTRY_VERSION, SPECS_BY_ID

    spec = SPECS_BY_ID.get("TAG-08")
    assert spec is not None and (spec.family, spec.level, spec.kind) == ("estado", "mayor", "code")
    assert REGISTRY_VERSION >= 6


# TAG-09 · lo que promete, lo hace ──────────────────────────────────────────
# Incidente del 2026-10-09: «Te paso el formulario para los datos de envío»
# dos turnos seguidos sin el formulario. El mismo detector del bot
# (`use_cases/promised_actions.py`): el componente prometido tiene que salir
# en ese turno (una tool del LLM o la red que lo mandó).

FORM_PROMISE = "Perfecto, contra entrega.\n\nTe paso el formulario para los datos de envío 🤍"


def test_tag09_a_promised_form_that_never_went_out_fails() -> None:
    t = traj(T(1, sent=["El set completo está en $45.000 🤍"]), T(2, sent=[FORM_PROMISE]))

    r = run("TAG-09", t)

    assert (r.verdict, r.turn) == ("falla", 2)
    assert "formulario" in r.evidence


def test_tag09_the_form_in_the_same_turn_keeps_the_promise() -> None:
    by_tool = traj(T(1, sent=[FORM_PROMISE], tools=[tool("request_shipping_details")]))
    by_net = traj(T(1, sent=[FORM_PROMISE], intents=["shipping_flow"]))

    assert run("TAG-09", by_tool).verdict == "pasa"
    assert run("TAG-09", by_net).verdict == "pasa"


def test_tag09_saying_the_order_is_registered_needs_the_order() -> None:
    claim = "¡Listo! Tu pedido quedó registrado 🤍"
    without = traj(T(1, sent=[claim]))
    registered_before = traj(T(1, tools=[tool("register_order")]), T(2, sent=[claim]))

    assert run("TAG-09", without).verdict == "falla"
    assert run("TAG-09", registered_before).verdict == "pasa"


def test_tag09_without_promises_is_not_judged() -> None:
    t = traj(T(1, sent=["¿Te paso el formulario para los datos de envío?"]))

    assert run("TAG-09", t).verdict == "no_aplica"


def test_tag09_is_a_critical_state_check() -> None:
    from src.plugins.chats.agent.sales_eval.scorecard.registry import SPECS_BY_ID

    spec = SPECS_BY_ID.get("TAG-09")
    assert spec is not None and (spec.family, spec.level, spec.kind) == ("estado", "critico", "code")
