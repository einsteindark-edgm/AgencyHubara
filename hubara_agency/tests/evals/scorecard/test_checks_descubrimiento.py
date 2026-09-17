"""Checks de la familia `descubrimiento` (HU-SC-1): preguntas antes del
catálogo, una pregunta por burbuja, catálogo pedido, búsqueda antes de nombrar,
producto elegido en el pedido y diseño antes que aroma."""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard.checks import CODE_CHECKS
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from tests.evals.scorecard.dsl import T, tool, traj
from tests.evals.scorecard.incidents import CATALOG_CTX

_NO_CATALOG = CheckContext()


def _run(check_id: str, t, ctx: CheckContext = _NO_CATALOG):
    return CODE_CHECKS[check_id](t, ctx)


# ── DES-01 ────────────────────────────────────────────────────────────────
def test_des01_two_questions_then_products_passes() -> None:
    t = traj(
        T(1, sent=["¡Buenos días! Bienvenido a Hubara"]),
        T(2, sent=["¿Es para ti o para regalar?"]),
        T(3, sent=["¿Qué estilo te gusta?"]),
        T(4, tools=[tool("present_products")]),
    )
    r = _run("DES-01", t)
    assert r.check_id == "DES-01"
    assert r.verdict == "pasa"


def test_des01_third_question_before_products_fails_on_that_turn() -> None:
    t = traj(
        T(1, sent=["¿Es para regalo?"]),
        T(2, sent=["¿Para qué espacio sería? ¿La sala o el dormitorio?"]),
        T(3, sent=["¿Prefieres algún color?"]),
        T(4, tools=[tool("present_products")]),
    )
    r = _run("DES-01", t)
    assert (r.verdict, r.turn) == ("falla", 3)
    assert "Prefieres algún color" in r.evidence


def test_des01_question_in_the_show_turn_does_not_count() -> None:
    t = traj(
        T(1, sent=["¿Es para regalo?"]),
        T(2, sent=["¿Qué estilo buscas?"]),
        T(3, sent=["¿Te gusta alguno?"], tools=[tool("present_products")]),
    )
    assert _run("DES-01", t).verdict == "pasa"


def test_des01_questions_outside_discovery_stage_do_not_count() -> None:
    t = traj(
        T(1, sent=["¿Es para regalo?"]),
        T(2, stage_in="variantes", sent=["¿Qué aroma?"]),
        T(3, stage_in="variantes", sent=["¿Qué color?"]),
        T(4, tools=[tool("present_products")]),
    )
    assert _run("DES-01", t).verdict == "pasa"


def test_des01_three_questions_and_never_shows_fails_on_third() -> None:
    t = traj(T(1, sent=["¿Es para regalo?"]), T(2, sent=["¿Qué espacio?"]), T(3, sent=["¿Qué color?"]))
    r = _run("DES-01", t)
    assert (r.verdict, r.turn) == ("falla", 3)


def test_des01_no_questions_and_no_products_is_not_applicable() -> None:
    assert _run("DES-01", traj(T(1, sent=["Claro, aquí te espero"]))).verdict == "no_aplica"


def test_des01_legacy_uses_ui_component_intents() -> None:
    t = traj(
        T(1, sent=["¿Es para regalo?"], stage_in=None),
        T(2, sent=["¿Qué espacio?"], stage_in=None),
        T(3, intents=["products_list"], stage_in=None),
        T(4, sent=["¿Qué color?"], stage_in=None),
        fidelity="legacy",
    )
    assert _run("DES-01", t).verdict == "pasa"


# ── DES-02 ────────────────────────────────────────────────────────────────
def test_des02_one_question_per_bubble_passes() -> None:
    t = traj(T(1, sent=["¿Es para regalo?", "¿Qué estilo buscas?"]))
    assert _run("DES-02", t).verdict == "pasa"


def test_des02_question_with_its_options_counts_as_one() -> None:
    t = traj(T(1, sent=["¿Para qué espacio sería? ¿La sala o el dormitorio?"]))
    assert _run("DES-02", t).verdict == "pasa"


def test_des02_two_questions_in_one_bubble_fails_on_that_turn() -> None:
    t = traj(T(1, sent=["Claro"]), T(2, sent=["¿Qué aroma prefieres? ¿Te gusta el azul?"]))
    r = _run("DES-02", t)
    assert (r.verdict, r.turn) == ("falla", 2)
    assert "Qué aroma prefieres" in r.evidence


def test_des02_without_text_is_not_applicable() -> None:
    assert _run("DES-02", traj(T(1, tools=[tool("present_products")]))).verdict == "no_aplica"


# ── DES-03 ────────────────────────────────────────────────────────────────
def test_des03_catalog_button_answered_with_products_passes() -> None:
    t = traj(T(1, inbound="[el cliente tocó el botón: Ver catálogo]", tools=[tool("present_products")]))
    assert _run("DES-03", t).verdict == "pasa"


def test_des03_request_answered_with_text_only_fails_on_that_turn() -> None:
    t = traj(
        T(1, inbound="hola", sent=["¡Buenos días!"]),
        T(2, inbound="muéstrame las velas", sent=["Tenemos velas de varios aromas"]),
    )
    r = _run("DES-03", t)
    assert (r.verdict, r.turn) == ("falla", 2)
    assert "muéstrame" in r.evidence


def test_des03_second_request_without_catalog_fails_on_second() -> None:
    t = traj(
        T(1, inbound="¿qué tienen?", tools=[tool("present_products")]),
        T(2, inbound="¿tienen otro catálogo?", sent=["Esas son todas"]),
    )
    r = _run("DES-03", t)
    assert (r.verdict, r.turn) == ("falla", 2)


def test_des03_handoff_next_step_mentioning_catalog_is_not_a_request() -> None:
    t = traj(
        T(1, trigger="handoff", inbound="Usuario respondió: ok. Siguiente paso: ofrecer el catálogo.",
          sent=["Perfecto"]),
    )
    assert _run("DES-03", t).verdict == "no_aplica"


def test_des03_without_request_is_not_applicable() -> None:
    assert _run("DES-03", traj(T(1, inbound="es para un regalo", sent=["¿Para quién?"]))).verdict == "no_aplica"


def test_des03_legacy_request_without_component_fails() -> None:
    t = traj(T(1, inbound="quiero ver el catálogo", sent=["Claro"], stage_in=None), fidelity="legacy")
    assert (_run("DES-03", t).verdict, _run("DES-03", t).turn) == ("falla", 1)


# ── DES-05 ────────────────────────────────────────────────────────────────
def test_des05_price_after_search_passes() -> None:
    t = traj(
        T(1, tools=[tool("search_products", q="café")]),
        T(2, sent=["La vela cuesta $89.000"]),
    )
    assert _run("DES-05", t).verdict == "pasa"


def test_des05_search_in_the_same_turn_counts() -> None:
    t = traj(T(1, sent=["La vela cuesta $89.000"], tools=[tool("search_products")]))
    assert _run("DES-05", t).verdict == "pasa"


def test_des05_price_before_any_search_fails_on_that_turn() -> None:
    t = traj(
        T(1, sent=["¡Buenos días!"]),
        T(2, sent=["Nuestras velas cuestan desde $45.000"]),
        T(3, tools=[tool("search_products")]),
    )
    r = _run("DES-05", t)
    assert (r.verdict, r.turn) == ("falla", 2)
    assert "$45.000" in r.evidence


def test_des05_product_title_without_search_fails_with_catalog() -> None:
    t = traj(T(1, sent=["El Cubo Love es de los más pedidos"]))
    r = _run("DES-05", t, CATALOG_CTX)
    assert (r.verdict, r.turn) == ("falla", 1)
    assert "Cubo Love" in r.evidence


def test_des05_titles_ignored_when_catalog_unavailable() -> None:
    t = traj(T(1, sent=["El Cubo Love es de los más pedidos"]))
    assert _run("DES-05", t, _NO_CATALOG).verdict == "no_aplica"


def test_des05_without_naming_is_not_applicable() -> None:
    assert _run("DES-05", traj(T(1, sent=["¿Es para regalo?"])), CATALOG_CTX).verdict == "no_aplica"


def test_des05_legacy_attempted_tool_counts_as_grounding() -> None:
    t = traj(T(1, sent=["Cuesta $89.000"], tools=[tool("search_products", ok=None)]), fidelity="legacy")
    assert _run("DES-05", t).verdict == "pasa"


# ── DES-08 ────────────────────────────────────────────────────────────────
def test_des08_list_selection_recorded_passes() -> None:
    t = traj(
        T(1, tools=[tool("present_products")]),
        T(2, inbound="[el cliente seleccionó: Cubo Love]", tools=[tool("set_order_slot", producto="cubo-love")]),
    )
    assert _run("DES-08", t, CATALOG_CTX).verdict == "pasa"


def test_des08_selection_without_slot_fails_on_that_turn() -> None:
    t = traj(
        T(1, tools=[tool("present_products")]),
        T(2, inbound="[el cliente seleccionó: Cubo Love]", sent=["¡Excelente elección!"]),
    )
    r = _run("DES-08", t, CATALOG_CTX)
    assert (r.verdict, r.turn) == ("falla", 2)


def test_des08_draft_with_product_counts_as_recorded() -> None:
    t = traj(
        T(1, tools=[tool("present_product_detail")]),
        T(2, inbound="[el cliente seleccionó: Vela Ángel]", draft={"producto": "vela-angel"}),
    )
    assert _run("DES-08", t, CATALOG_CTX).verdict == "pasa"


def test_des08_customer_names_shown_product_without_slot_fails() -> None:
    t = traj(
        T(1, tools=[tool("present_products")]),
        T(2, inbound="me gusta el duo zodiacal", sent=["¡Qué bonita elección!"]),
    )
    r = _run("DES-08", t, CATALOG_CTX)
    assert (r.verdict, r.turn) == ("falla", 2)


def test_des08_title_mentioned_before_any_catalog_is_not_a_choice() -> None:
    t = traj(T(1, inbound="¿tienen el Cubo Love?", tools=[tool("search_products")]))
    assert _run("DES-08", t, CATALOG_CTX).verdict == "no_aplica"


def test_des08_vague_choice_without_title_is_not_applicable() -> None:
    t = traj(T(1, tools=[tool("present_products")]), T(2, inbound="El primero azulito"))
    assert _run("DES-08", t, CATALOG_CTX).verdict == "no_aplica"


def test_des08_selection_from_variant_picker_is_not_a_product_choice() -> None:
    t = traj(
        T(1, tools=[tool("present_products")]),
        T(2, tools=[tool("present_variant_picker")]),
        T(3, inbound="[el cliente seleccionó: Azul]"),
    )
    assert _run("DES-08", t, CATALOG_CTX).verdict == "no_aplica"


def test_des08_legacy_with_slot_tool_passes() -> None:
    t = traj(
        T(1, intents=["products_list"]),
        T(2, inbound="[el cliente seleccionó: Cubo Love]", tools=[tool("set_order_slot", ok=None)]),
        fidelity="legacy",
    )
    assert _run("DES-08", t, CATALOG_CTX).verdict == "pasa"


def test_des08_legacy_without_slot_tool_is_unknown() -> None:
    t = traj(
        T(1, intents=["products_list"]),
        T(2, inbound="[el cliente seleccionó: Cubo Love]"),
        fidelity="legacy",
    )
    assert _run("DES-08", t, CATALOG_CTX).verdict == "desconocido"


# ── DES-09 ────────────────────────────────────────────────────────────────
def test_des09_design_questions_before_products_pass() -> None:
    t = traj(T(1, sent=["¿Es para regalo?"]), T(2, sent=["¿Qué estilo te gusta?"]), T(3, tools=[tool("present_products")]))
    assert _run("DES-09", t).verdict == "pasa"


def test_des09_aroma_question_before_products_fails_on_that_turn() -> None:
    t = traj(
        T(1, sent=["¡Buenos días!"]),
        T(2, sent=["¿Qué aroma le gusta a la persona?"]),
        T(3, tools=[tool("present_products")]),
    )
    r = _run("DES-09", t)
    assert (r.verdict, r.turn) == ("falla", 2)
    assert "aroma" in r.evidence


def test_des09_aroma_question_after_products_passes() -> None:
    t = traj(T(1, sent=["¿Es para regalo?"]), T(2, tools=[tool("present_products")]), T(3, sent=["¿Qué aroma prefieres?"]))
    assert _run("DES-09", t).verdict == "pasa"


def test_des09_products_on_first_turn_is_not_applicable() -> None:
    t = traj(T(1, tools=[tool("present_products")]), T(2, sent=["¿Qué aromas te gustan?"]))
    assert _run("DES-09", t).verdict == "no_aplica"


def test_des05_policy_threshold_and_shipping_amounts_are_not_catalog_prices() -> None:
    """Primer informe (9-15 sep): el umbral de contra entrega reprobó DES-05 como
    si fuera un precio de producto. Montos de política de pago o de envío no
    salen del catálogo."""
    t = traj(T(1, sent=[
        "Tenemos tres formas de pago:\n1. *Contra entrega*: pagas al recibir, aplica para compras superiores a $45.000.\n"
        "2. *Link de pago*: tiene un recargo de $3.000. El envío a Bogotá sale en $12.000.",
    ]))
    assert _run("DES-05", t).verdict == "no_aplica"


def test_des05_product_price_next_to_a_policy_amount_still_fails() -> None:
    t = traj(T(1, sent=["El Cubo Love cuesta $89.000 y el envío sale en $12.000"]))
    r = _run("DES-05", t)
    assert (r.verdict, r.turn) == ("falla", 1)
    assert "$89.000" in r.evidence


# ── DES-10 ────────────────────────────────────────────────────────────────
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext as _Ctx  # noqa: E402

_PRICES_CTX = _Ctx(product_titles=("Trilogía del Terror",), catalog_available=True, catalog_prices=(49500, 16000))


def test_des10_non_catalog_price_in_text_fails() -> None:
    t = traj(T(1, sent=["El set de la Trilogía del Terror tiene un valor de *$45.000 COP*."]))
    r = _run("DES-10", t, _PRICES_CTX)
    assert (r.verdict, r.turn) == ("falla", 1)
    assert "$45.000" in r.evidence


def test_des10_quick_replies_body_is_audited_too() -> None:
    t = traj(T(1, tools=[tool("send_quick_replies", body="Vale $45.000. ¿Lo dejamos así?")]))
    assert _run("DES-10", t, _PRICES_CTX).verdict == "falla"


def test_des10_catalog_price_and_policy_amounts_pass() -> None:
    t = traj(T(1, sent=["El set vale $49.500. El contra entrega aplica desde $45.000 en productos; el envío mínimo nacional es $16.940."]))
    assert _run("DES-10", t, _PRICES_CTX).verdict == "pasa"


def test_des10_without_amounts_is_not_applicable() -> None:
    assert _run("DES-10", traj(T(1, sent=["¿Qué aroma te gusta?"])), _PRICES_CTX).verdict == "no_aplica"


def test_des10_without_catalog_is_unknown() -> None:
    t = traj(T(1, sent=["Vale $45.000"]))
    assert _run("DES-10", t, _Ctx()).verdict == "desconocido"
