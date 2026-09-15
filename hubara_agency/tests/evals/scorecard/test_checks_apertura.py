"""Checks de la familia `apertura` (HU-SC-1): saludo, apertura prohibida,
catálogo ofrecido y no volver a saludar."""
from __future__ import annotations

from dataclasses import replace

from src.plugins.chats.agent.sales_eval.scorecard.checks import CODE_CHECKS
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from tests.evals.scorecard.dsl import T, tool, traj

_GREETING = "¡Buenos días! Bienvenido a *Hubara*, velas artesanales."


def _run(check_id: str, t):
    return CODE_CHECKS[check_id](t, CheckContext())


def _without_first_contact(t):
    """Legacy real: el JSONL no dice si era primer contacto (el DSL lo deriva)."""
    return replace(t, turns=tuple(replace(x, first_contact=None) for x in t.turns))


# ── APE-01 ────────────────────────────────────────────────────────────────
def test_ape01_greeting_with_brand_passes() -> None:
    r = _run("APE-01", traj(T(1, sent=[_GREETING]), T(2, sent=["¿Qué buscas?"])))
    assert r.check_id == "APE-01"
    assert r.verdict == "pasa"


def test_ape01_first_text_without_greeting_fails_on_that_turn() -> None:
    t = traj(T(1, sent=[]), T(2, sent=["Claro, aquí tienes el catálogo de Hubara"]))
    r = _run("APE-01", t)
    assert (r.verdict, r.turn) == ("falla", 2)
    assert "aquí tienes" in r.evidence


def test_ape01_greeting_without_brand_fails() -> None:
    r = _run("APE-01", traj(T(1, sent=["¡Buenas tardes! ¿En qué te ayudo?"])))
    assert (r.verdict, r.turn) == ("falla", 1)


def test_ape01_first_contact_without_any_text_fails_on_turn_one() -> None:
    r = _run("APE-01", traj(T(1, tools=[tool("send_quick_replies")]), T(2)))
    assert (r.verdict, r.turn) == ("falla", 1)
    assert "no se envió saludo" in r.evidence


def test_ape01_returning_customer_is_not_applicable() -> None:
    r = _run("APE-01", traj(T(1, first_contact=False, sent=["Claro que sí"])))
    assert r.verdict == "no_aplica"


def test_ape01_legacy_without_first_contact_is_unknown() -> None:
    t = _without_first_contact(traj(T(1, sent=[_GREETING]), fidelity="legacy"))
    r = _run("APE-01", t)
    assert r.verdict == "desconocido"


# ── APE-02 ────────────────────────────────────────────────────────────────
def test_ape02_greeting_by_time_passes() -> None:
    assert _run("APE-02", traj(T(1, sent=[_GREETING]))).verdict == "pasa"


def test_ape02_hola_opener_fails() -> None:
    r = _run("APE-02", traj(T(1, sent=["¡Hola! Bienvenido a Hubara"])))
    assert (r.verdict, r.turn) == ("falla", 1)
    assert "Hola" in r.evidence


def test_ape02_buen_dia_opener_fails_on_first_text_turn() -> None:
    r = _run("APE-02", traj(T(1), T(2, sent=["Buen día, bienvenido a Hubara"])))
    assert (r.verdict, r.turn) == ("falla", 2)


def test_ape02_returning_customer_is_not_applicable() -> None:
    r = _run("APE-02", traj(T(1, first_contact=False, sent=["¡Hola!"])))
    assert r.verdict == "no_aplica"


def test_ape02_unknown_first_contact_is_unknown() -> None:
    t = _without_first_contact(traj(T(1, sent=["¡Hola!"]), fidelity="legacy"))
    assert _run("APE-02", t).verdict == "desconocido"


# ── APE-03 ────────────────────────────────────────────────────────────────
def test_ape03_quick_replies_on_opening_passes() -> None:
    t = traj(T(1, sent=[_GREETING], tools=[tool("send_quick_replies")]))
    assert _run("APE-03", t).verdict == "pasa"


def test_ape03_products_on_opening_passes() -> None:
    t = traj(T(1, sent=[_GREETING], tools=[tool("present_products")]))
    assert _run("APE-03", t).verdict == "pasa"


def test_ape03_opening_without_catalog_offer_fails_on_turn_one() -> None:
    t = traj(T(1, sent=[_GREETING], tools=[tool("send_quick_replies", ok=False, error="catalog_selector")]))
    r = _run("APE-03", t)
    assert (r.verdict, r.turn) == ("falla", 1)


def test_ape03_returning_customer_is_not_applicable() -> None:
    assert _run("APE-03", traj(T(1, first_contact=False))).verdict == "no_aplica"


def test_ape03_legacy_opening_without_components_is_unknown() -> None:
    t = traj(T(1, sent=[_GREETING], first_contact=True), fidelity="legacy")
    assert _run("APE-03", t).verdict == "desconocido"


# ── APE-04 ────────────────────────────────────────────────────────────────
def test_ape04_returning_customer_without_welcome_passes() -> None:
    t = traj(T(1, first_contact=False, sent=["¡Buenas tardes! ¿Retomamos tu pedido?"]))
    assert _run("APE-04", t).verdict == "pasa"


def test_ape04_returning_customer_welcomed_again_fails_on_that_turn() -> None:
    t = traj(
        T(1, first_contact=False, sent=["Claro, ya te ayudo"]),
        T(2, first_contact=False, sent=["¡Bienvenida a Hubara! Somos velas artesanales"]),
    )
    r = _run("APE-04", t)
    assert (r.verdict, r.turn) == ("falla", 2)
    assert "Bienvenida" in r.evidence


def test_ape04_first_contact_is_not_applicable() -> None:
    assert _run("APE-04", traj(T(1, sent=[_GREETING]))).verdict == "no_aplica"


def test_ape04_unknown_first_contact_is_unknown() -> None:
    t = _without_first_contact(traj(T(1, sent=[_GREETING]), fidelity="legacy"))
    assert _run("APE-04", t).verdict == "desconocido"
