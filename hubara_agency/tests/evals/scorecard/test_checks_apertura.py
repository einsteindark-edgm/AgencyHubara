"""Checks de la familia `apertura` (HU-SC-1): saludo, apertura prohibida,
catálogo ofrecido y no volver a saludar."""
from __future__ import annotations

from dataclasses import replace

from src.plugins.chats.agent.sales_eval.scorecard.checks import CODE_CHECKS
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import build_trajectory
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


_OUR_FOLLOW_UP = (
    "[El cliente responde a este mensaje que le enviamos: «Hola, te escribe Liliana, asesora de Hubara, para hacer "
    "seguimiento a tu consulta de velas. ¿En cuál estás interesada?»]\nHola"
)


def test_ape01_brand_our_own_message_already_said_is_not_required() -> None:
    """Incidente del 2026-10-09 (···9824, ep_001): el cliente contestó el
    seguimiento de una asesora («te escribe Liliana, asesora de Hubara») y el
    bot saludó por la hora sin repetir la marca. La marca ya la dijo nuestro
    mensaje: no se le cobra al bot (operador)."""
    t = traj(T(1, inbound=_OUR_FOLLOW_UP, sent=["Buenas tardes 🤍 Con gusto te ayudo con las velas de Halloween."]))
    assert _run("APE-01", t).verdict == "pasa"


def test_ape01_our_message_without_the_brand_still_asks_for_it() -> None:
    inbound = "[El cliente responde a este mensaje que le enviamos: «Hola, ¿seguimos con tu pedido?»]\nHola"
    t = traj(T(1, inbound=inbound, sent=["Buenas tardes, con gusto te ayudo."]))
    assert _run("APE-01", t).verdict == "falla"


def test_ape01_answering_our_message_still_needs_the_greeting_by_hour() -> None:
    t = traj(T(1, inbound=_OUR_FOLLOW_UP, sent=["Con gusto te ayudo con las velas de Halloween."]))
    r = _run("APE-01", t)
    assert r.verdict == "falla"
    assert "saludo por hora" in r.evidence
    assert "marca" not in r.evidence


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


# ── APE-01/02 juzgan lo primero que LEYÓ el cliente ───────────────────────
# Caso 4567 del laboratorio (caso-fotos-0929-r3, turno 1 del bot nuevo): el
# turno fue solo la lista y el saludo con la marca iba en su texto. APE-01
# falló «primer texto sin marca Hubara «Buenas tardes 🤍»», leyendo el
# complemento que salió DESPUÉS de la lista. En un turno sale primero el
# texto y después las tarjetas; el complemento, al final.
_LIST_GREETING = "Buenos días, bienvenido a *Hubara*, velas artesanales.\n\nEsta es nuestra colección de Halloween:"


def test_ape01_reads_the_greeting_inside_the_first_list() -> None:
    t = traj(T(1, tools=[tool("search_products", q="halloween"), tool("present_products", intro_text=_LIST_GREETING)]))
    r = _run("APE-01", t)
    assert (r.verdict, r.turn) == ("pasa", 1)
    assert "Buenos días" in r.evidence


def test_ape01_the_complement_is_read_after_the_cards_of_its_turn() -> None:
    records = [
        {"turn": 1, "trigger": "customer", "first_contact": True, "sent_texts": [],
         "tools": [{"name": "present_products", "ok": True, "args": {"intro_text": _LIST_GREETING}}]},
        {"turn": 1, "trigger": "complement", "sent_texts": ["Buenas tardes 🤍"],
         "tools": [{"name": "send_reply", "ok": True, "args": {"text": "Buenas tardes 🤍"}}]},
    ]
    t = build_trajectory(records, session_id="wa_573001234567", episode={"episode_id": "ep_001"})
    r = _run("APE-01", t)
    assert (r.verdict, r.turn) == ("pasa", 1)
    assert "Buenos días" in r.evidence


def test_ape01_a_text_of_the_same_turn_is_read_before_its_cards() -> None:
    t = traj(T(1, sent=["¿Buscas algo en especial?"], tools=[tool("present_products", intro_text=_LIST_GREETING)]))
    r = _run("APE-01", t)
    assert (r.verdict, r.turn) == ("falla", 1)
    assert "Buscas algo" in r.evidence


def test_ape01_a_card_that_did_not_go_out_is_not_read() -> None:
    refused = tool("present_products", ok=False, error="no_valid_handles", intro_text=_LIST_GREETING)
    t = traj(T(1, tools=[refused]), T(2, sent=["Claro, aquí tienes el catálogo de Hubara"]))
    r = _run("APE-01", t)
    assert (r.verdict, r.turn) == ("falla", 2)


def test_ape02_reads_the_opening_of_the_first_list() -> None:
    t = traj(T(1, tools=[tool("present_products", intro_text="¡Hola! Estas son nuestras velas de Halloween:")]))
    r = _run("APE-02", t)
    assert (r.verdict, r.turn) == ("falla", 1)
    assert "Hola" in r.evidence


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


def test_ape03_answering_our_message_the_opening_was_ours() -> None:
    """Incidente del 2026-10-09 (···9824): el cliente contesta el seguimiento de
    la asesora; la apertura ya la hicimos nosotros y él llega con un tema. No
    se le piden botones ni catálogo al bot."""
    t = traj(T(1, inbound=_OUR_FOLLOW_UP, sent=["Buenas tardes 🤍 ¿Te interesa la trilogía completa?"]))
    assert _run("APE-03", t).verdict == "no_aplica"


def test_ape03_a_first_message_of_the_customer_still_needs_buttons_or_catalog() -> None:
    t = traj(T(1, inbound="Hola", sent=["¡Buenos días! Bienvenido a Hubara. ¿Qué buscas?"]))
    assert _run("APE-03", t).verdict == "falla"
