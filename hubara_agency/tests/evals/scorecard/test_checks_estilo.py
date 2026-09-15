"""Checks de la familia `estilo` (HU-SC-1): voseo, guion largo y emojis, texto
administrativo, promesas de entrega y narración descartada."""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard.checks import CODE_CHECKS
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from tests.evals.scorecard.dsl import T, tool, traj


def _run(check_id: str, t):
    return CODE_CHECKS[check_id](t, CheckContext())


# ── EST-01 ────────────────────────────────────────────────────────────────
def test_est01_colombian_tuteo_passes() -> None:
    r = _run("EST-01", traj(T(1, sent=["¿Quieres que te muestre las velas?"])))
    assert r.check_id == "EST-01"
    assert r.verdict == "pasa"


def test_est01_voseo_form_fails_on_that_turn_naming_it() -> None:
    t = traj(T(1, sent=["Claro"]), T(2, sent=["Dale, ya te lo muestro"]))
    r = _run("EST-01", t)
    assert (r.verdict, r.turn) == ("falla", 2)
    assert "Dale" in r.evidence


def test_est01_without_text_is_not_applicable() -> None:
    assert _run("EST-01", traj(T(1))).verdict == "no_aplica"


# ── EST-02 ────────────────────────────────────────────────────────────────
def test_est02_allowed_emoji_passes() -> None:
    assert _run("EST-02", traj(T(1, sent=["Gracias por escribirnos 🤍"]))).verdict == "pasa"


def test_est02_em_dash_fails_on_that_turn() -> None:
    t = traj(T(1, sent=["Claro"]), T(2, sent=["Velas artesanales — hechas a mano"]))
    r = _run("EST-02", t)
    assert (r.verdict, r.turn) == ("falla", 2)


def test_est02_disallowed_emoji_fails() -> None:
    r = _run("EST-02", traj(T(1, sent=["¡Qué bien! 😊"])))
    assert (r.verdict, r.turn) == ("falla", 1)
    assert "😊" in r.evidence


def test_est02_two_emojis_in_one_paragraph_fails() -> None:
    assert _run("EST-02", traj(T(1, sent=["Gracias 🤍 ✨"]))).verdict == "falla"


def test_est02_one_emoji_per_paragraph_passes() -> None:
    assert _run("EST-02", traj(T(1, sent=["Gracias 🤍\n\nTe esperamos ✨"]))).verdict == "pasa"


def test_est02_without_text_is_not_applicable() -> None:
    assert _run("EST-02", traj(T(1))).verdict == "no_aplica"


# ── EST-03 ────────────────────────────────────────────────────────────────
def test_est03_sales_text_passes() -> None:
    assert _run("EST-03", traj(T(1, sent=["¿Te muestro el catálogo?"]))).verdict == "pasa"


def test_est03_admin_report_to_customer_fails_on_that_turn() -> None:
    t = traj(T(1, sent=["Claro"]), T(2, sent=["La conversación queda etiquetada como INTERESADO"]))
    r = _run("EST-03", t)
    assert (r.verdict, r.turn) == ("falla", 2)


def test_est03_without_text_is_not_applicable() -> None:
    assert _run("EST-03", traj(T(1))).verdict == "no_aplica"


# ── EST-03b ───────────────────────────────────────────────────────────────
def test_est03b_guard_idle_passes() -> None:
    assert _run("EST-03b", traj(T(1, sent=["Claro"]))).verdict == "pasa"


def test_est03b_guard_acted_fails_on_that_turn() -> None:
    t = traj(T(1, sent=["Claro"]), T(2, guards=["admin_text_guard"]))
    assert (_run("EST-03b", t).verdict, _run("EST-03b", t).turn) == ("falla", 2)


def test_est03b_suppressed_by_admin_guard_fails() -> None:
    t = traj(T(1, llm="Etiqueté como INTERESADO", suppressed="admin_text_guard"))
    assert (_run("EST-03b", t).verdict, _run("EST-03b", t).turn) == ("falla", 1)


def test_est03b_legacy_is_unknown() -> None:
    assert _run("EST-03b", traj(T(1, sent=["Claro"]), fidelity="legacy")).verdict == "desconocido"


# ── EST-05 ────────────────────────────────────────────────────────────────
def test_est05_no_delivery_promise_passes() -> None:
    t = traj(T(1, sent=["Te llega cuando la transportadora lo entregue"]))
    assert _run("EST-05", t).verdict == "pasa"


def test_est05_concrete_delivery_time_fails_on_that_turn() -> None:
    t = traj(T(1, sent=["Claro"]), T(2, sent=["Tranquila, te llega en 3 días"]))
    r = _run("EST-05", t)
    assert (r.verdict, r.turn) == ("falla", 2)
    assert "3 días" in r.evidence


def test_est05_delivery_tomorrow_fails() -> None:
    assert _run("EST-05", traj(T(1, sent=["Te entregamos mañana sin falta"]))).verdict == "falla"


def test_est05_time_word_without_delivery_verb_passes() -> None:
    assert _run("EST-05", traj(T(1, sent=["Hoy tenemos velas nuevas en 2 días de producción"]))).verdict == "pasa"


def test_est05_without_text_is_not_applicable() -> None:
    assert _run("EST-05", traj(T(1))).verdict == "no_aplica"


# ── EST-06 ────────────────────────────────────────────────────────────────
def test_est06_without_narration_passes() -> None:
    assert _run("EST-06", traj(T(1, sent=["Claro"], tools=[tool("present_products")]))).verdict == "pasa"


def test_est06_discarded_narration_fails_on_that_turn() -> None:
    t = traj(T(1, sent=["Claro"]), T(2, narration=["Te dejo el formulario."], tools=[tool("request_shipping_details")]))
    r = _run("EST-06", t)
    assert (r.verdict, r.turn) == ("falla", 2)
    assert "Te dejo el formulario" in r.evidence


def test_est06_legacy_is_unknown() -> None:
    assert _run("EST-06", traj(T(1, sent=["Claro"]), fidelity="legacy")).verdict == "desconocido"
