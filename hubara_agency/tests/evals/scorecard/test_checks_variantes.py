"""Checks de variantes (VAR-*)."""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard.checks import CODE_CHECKS
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from tests.evals.scorecard.dsl import T, tool, traj
from tests.evals.scorecard.incidents import CATALOG_CTX

ENUM = "Tenemos estos aromas: Lavanda, Café, Sándalo, Limoncillo y Chanel."


def run(cid, t, ctx=CATALOG_CTX):
    return CODE_CHECKS[cid](t, ctx)


# VAR-01 / VAR-01b ───────────────────────────────────────────────────────────
def test_var01_enumerated_aromas_in_sent_text_fails() -> None:
    r = run("VAR-01", traj(T(1, sent=["Hola"]), T(2, sent=[ENUM])))
    assert (r.verdict, r.turn) == ("falla", 2)


def test_var01_suppressed_enumeration_passes_and_needs_catalog() -> None:
    t = traj(T(1, llm=ENUM, sent=[], suppressed="variant_enumeration_guard", guards=["variant_enumeration_guard"]),
             T(2, sent=["¿Cuál te gusta?"]))
    assert run("VAR-01", t).verdict == "pasa"
    assert run("VAR-01", t, CheckContext()).verdict == "desconocido"


def test_var01b_guard_fired_fails() -> None:
    t = traj(T(1), T(2, guards=["variant_enumeration_guard"]))
    assert (run("VAR-01b", t).verdict, run("VAR-01b", t).turn) == ("falla", 2)
    assert run("VAR-01b", traj(T(1))).verdict == "pasa"


# VAR-02 / VAR-03 ────────────────────────────────────────────────────────────
def test_var02_rejected_picker_fails_and_valid_picker_passes() -> None:
    bad = traj(T(1, tools=[tool("present_variant_picker", ok=False, error="invalid_options")]))
    assert run("VAR-02", bad).verdict == "falla"
    assert run("VAR-02", traj(T(1, tools=[tool("present_variant_picker")]))).verdict == "pasa"
    assert run("VAR-02", traj(T(1))).verdict == "no_aplica"


def test_var03_duplicate_picker_same_type_fails() -> None:
    dup = traj(T(1, tools=[tool("present_variant_picker", variant_type="scent"),
                           tool("present_variant_picker", variant_type="scent")]))
    assert run("VAR-03", dup).verdict == "falla"
    both = traj(T(1, tools=[tool("present_variant_picker", variant_type="scent"),
                            tool("present_variant_picker", variant_type="color")]))
    assert run("VAR-03", both).verdict == "pasa"


# VAR-04 ─────────────────────────────────────────────────────────────────────
def test_var04_picker_choice_without_slot_fails() -> None:
    t = traj(T(1, tools=[tool("present_variant_picker")]),
             T(2, inbound="[el cliente seleccionó: Lavanda]", sent=["¡Buena elección!"]))
    r = run("VAR-04", t)
    assert (r.verdict, r.turn) == ("falla", 2)


def test_var04_picker_choice_recorded_passes() -> None:
    t = traj(T(1, tools=[tool("present_variant_picker")]),
             T(2, inbound="[el cliente seleccionó: Lavanda]", tools=[tool("set_order_slot", aroma="Lavanda")]))
    assert run("VAR-04", t).verdict == "pasa"


# VAR-06 ─────────────────────────────────────────────────────────────────────
def test_var06_reasking_quantity_fails() -> None:
    t = traj(T(1, draft={"producto": "x", "cantidad": "2"}), T(2, sent=["¿Cuántas unidades deseas?"]))
    r = run("VAR-06", t)
    assert (r.verdict, r.turn) == ("falla", 2)


def test_var06_asking_before_known_passes() -> None:
    t = traj(T(1, sent=["¿Cuántas unidades deseas?"]), T(2, draft={"cantidad": "2"}))
    assert run("VAR-06", t).verdict == "no_aplica" or run("VAR-06", t).verdict == "pasa"
    assert run("VAR-06", traj(T(1), fidelity="legacy")).verdict == "desconocido"


# VAR-07 ─────────────────────────────────────────────────────────────────────
def test_var07_form_without_any_price_before_fails() -> None:
    t = traj(T(1, sent=["¿Te lo dejo en azul?"]), T(2, tools=[tool("request_shipping_details")]))
    r = run("VAR-07", t)
    assert (r.verdict, r.turn) == ("falla", 2)


def test_var07_price_in_text_or_catalog_before_passes() -> None:
    by_text = traj(T(1, sent=["El Cubo Love cuesta $89.000"]), T(2, tools=[tool("request_shipping_details")]))
    by_list = traj(T(1, tools=[tool("present_products")]), T(2, tools=[tool("request_shipping_details")]))
    assert run("VAR-07", by_text).verdict == "pasa"
    assert run("VAR-07", by_list).verdict == "pasa"
    assert run("VAR-07", traj(T(1))).verdict == "no_aplica"


# VAR-09 ─────────────────────────────────────────────────────────────────────
def test_var09_rejected_selector_buttons_fail() -> None:
    t = traj(T(1, tools=[tool("send_quick_replies", ok=False, error="catalog_choice_not_allowed")]))
    assert run("VAR-09", t).verdict == "falla"
    partial = traj(T(1, tools=[tool("send_quick_replies", notes=["rejected_buttons:2"])]))
    assert run("VAR-09", partial).verdict == "falla"
    assert run("VAR-09", traj(T(1, tools=[tool("send_quick_replies")]))).verdict == "pasa"
    assert run("VAR-09", traj(T(1))).verdict == "no_aplica"
