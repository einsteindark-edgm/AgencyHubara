"""Checks de la familia `envio` (HU-SC-1): formulario una vez y con respuesta,
datos capturados, sin re-preguntar, sin datos bancarios ni valor definitivo y
nombre de quien recibe."""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard.checks import CODE_CHECKS
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from tests.evals.scorecard.dsl import T, tool, traj

_FORM = "request_shipping_details"
_FORM_DATA = "[datos de envío recibidos] ciudad=Cali; direccion=Calle 1"


def _run(check_id: str, t):
    return CODE_CHECKS[check_id](t, CheckContext())


# ── ENV-01 ────────────────────────────────────────────────────────────────
def test_env01_form_sent_once_passes() -> None:
    r = _run("ENV-01", traj(T(1, sent=["Te dejo el formulario"], tools=[tool(_FORM)])))
    assert r.check_id == "ENV-01"
    assert r.verdict == "pasa"


def test_env01_form_sent_twice_fails_on_second() -> None:
    t = traj(T(1, tools=[tool(_FORM)]), T(2, sent=["Listo"]), T(3, tools=[tool(_FORM)]))
    r = _run("ENV-01", t)
    assert (r.verdict, r.turn) == ("falla", 3)


def test_env01_rejected_form_does_not_count() -> None:
    t = traj(T(1, tools=[tool(_FORM)]), T(2, tools=[tool(_FORM, ok=False, error="customer_deferred")]))
    assert _run("ENV-01", t).verdict == "pasa"


def test_env01_without_form_is_not_applicable() -> None:
    assert _run("ENV-01", traj(T(1, sent=["Hola"]))).verdict == "no_aplica"


def test_env01_legacy_counts_ui_components() -> None:
    t = traj(T(1, intents=["shipping_flow"]), T(2, intents=["shipping_flow"]), fidelity="legacy")
    assert (_run("ENV-01", t).verdict, _run("ENV-01", t).turn) == ("falla", 2)


# ── ENV-02 ────────────────────────────────────────────────────────────────
def test_env02_form_with_reply_text_passes() -> None:
    t = traj(T(1, inbound="sí, lo quiero", sent=["¡Perfecto! Te dejo el formulario"], tools=[tool(_FORM)]))
    assert _run("ENV-02", t).verdict == "pasa"


def test_env02_bare_form_after_handoff_fails_with_discarded_narration() -> None:
    t = traj(
        T(1, inbound="hola", sent=["¡Buenos días!"]),
        T(2, trigger="handoff", inbound="Usuario respondió: voy en camino", tools=[tool(_FORM)],
          narration=["Perfecto, te dejo el formulario."]),
    )
    r = _run("ENV-02", t)
    assert (r.verdict, r.turn) == ("falla", 2)
    assert "te dejo el formulario" in r.evidence


def test_env02_form_after_confirm_button_is_not_applicable() -> None:
    t = traj(T(1, inbound="[el cliente tocó el botón: ✅ Confirmar]", tools=[tool(_FORM)]))
    assert _run("ENV-02", t).verdict == "no_aplica"


def test_env02_without_form_is_not_applicable() -> None:
    assert _run("ENV-02", traj(T(1, inbound="hola", sent=["¡Buenos días!"]))).verdict == "no_aplica"


def test_env02_legacy_bare_form_fails() -> None:
    t = traj(T(1, inbound="sí", intents=["shipping_flow"]), fidelity="legacy")
    assert (_run("ENV-02", t).verdict, _run("ENV-02", t).turn) == ("falla", 1)


# ── ENV-03 ────────────────────────────────────────────────────────────────
def test_env03_form_data_recorded_passes() -> None:
    t = traj(T(1, inbound=_FORM_DATA, tools=[tool("set_order_slot", ciudad="Cali")]))
    assert _run("ENV-03", t).verdict == "pasa"


def test_env03_form_data_not_recorded_fails_on_that_turn() -> None:
    t = traj(T(1, tools=[tool(_FORM)]), T(2, inbound=_FORM_DATA, sent=["¡Gracias!"]))
    r = _run("ENV-03", t)
    assert (r.verdict, r.turn) == ("falla", 2)


def test_env03_rejected_slot_call_fails() -> None:
    t = traj(T(1, inbound=_FORM_DATA, tools=[tool("set_order_slot", ok=False, error="invalid")]))
    assert _run("ENV-03", t).verdict == "falla"


def test_env03_without_form_data_is_not_applicable() -> None:
    assert _run("ENV-03", traj(T(1, inbound="hola"))).verdict == "no_aplica"


def test_env03_legacy_slot_name_present_passes() -> None:
    t = traj(T(1, inbound=_FORM_DATA, tools=[tool("set_order_slot", ok=None)]), fidelity="legacy")
    assert _run("ENV-03", t).verdict == "pasa"


# ── ENV-04 ────────────────────────────────────────────────────────────────
def test_env04_asking_missing_field_passes() -> None:
    t = traj(
        T(1, draft={"ciudad": "Cali"}),
        T(2, sent=["¿Me compartes la dirección?"], draft={"ciudad": "Cali"}),
    )
    assert _run("ENV-04", t).verdict == "pasa"


def test_env04_reasking_known_city_fails_on_that_turn() -> None:
    t = traj(
        T(1, draft={"ciudad": "Cali"}),
        T(2, sent=["Perfecto"], draft={"ciudad": "Cali"}),
        T(3, sent=["¿En qué ciudad recibes?"], draft={"ciudad": "Cali"}),
    )
    r = _run("ENV-04", t)
    assert (r.verdict, r.turn) == ("falla", 3)
    assert "ciudad" in r.evidence


def test_env04_field_captured_in_same_turn_is_not_known_yet() -> None:
    t = traj(T(1, sent=["¿Quién recibe el pedido?"], draft={"nombre_recibe": "Ana"}))
    assert _run("ENV-04", t).verdict == "pasa"


def test_env04_without_shipping_data_is_not_applicable() -> None:
    t = traj(T(1, draft={"producto": "cubo-love"}), T(2, sent=["¿En qué ciudad?"]))
    assert _run("ENV-04", t).verdict == "no_aplica"


def test_env04_legacy_is_unknown() -> None:
    t = traj(T(1, sent=["¿En qué ciudad?"]), fidelity="legacy")
    assert _run("ENV-04", t).verdict == "desconocido"


# ── ENV-05 ────────────────────────────────────────────────────────────────
def test_env05_clean_texts_pass() -> None:
    t = traj(T(1, sent=["El valor del envío se confirma al despachar"]))
    assert _run("ENV-05", t).verdict == "pasa"


def test_env05_bank_account_in_text_fails_on_that_turn() -> None:
    t = traj(T(1, sent=["¡Perfecto!"]), T(2, sent=["Puedes transferir a la cuenta de ahorros 123456"]))
    r = _run("ENV-05", t)
    assert (r.verdict, r.turn) == ("falla", 2)
    assert "cuenta de ahorros" in r.evidence


def test_env05_definitive_shipping_value_fails() -> None:
    r = _run("ENV-05", traj(T(1, sent=["El envío cuesta $12.000 a Cali"])))
    assert (r.verdict, r.turn) == ("falla", 1)


def test_env05_without_text_is_not_applicable() -> None:
    assert _run("ENV-05", traj(T(1, tools=[tool(_FORM)]))).verdict == "no_aplica"


# ── ENV-07 ────────────────────────────────────────────────────────────────
def test_env07_register_without_rejection_passes() -> None:
    assert _run("ENV-07", traj(T(1, tools=[tool("register_order")]))).verdict == "pasa"


def test_env07_missing_receiver_name_fails_on_that_turn() -> None:
    t = traj(
        T(1, sent=["Listo"]),
        T(2, tools=[tool("register_order", ok=False, error="missing_receiver_name")]),
        T(3, tools=[tool("register_order")]),
    )
    r = _run("ENV-07", t)
    assert (r.verdict, r.turn) == ("falla", 2)


def test_env07_without_register_is_not_applicable() -> None:
    assert _run("ENV-07", traj(T(1, sent=["Hola"]))).verdict == "no_aplica"


def test_env07_legacy_is_unknown() -> None:
    t = traj(T(1, tools=[tool("register_order", ok=None)]), fidelity="legacy")
    assert _run("ENV-07", t).verdict == "desconocido"


def test_env05_naming_payment_methods_without_numbers_passes() -> None:
    t = traj(T(1, sent=["Puedes pagar contra entrega, por Nequi o con link de pago. ¿Cuál prefieres?"]))
    assert CODE_CHECKS["ENV-05"](t, CheckContext()).verdict == "pasa"


def test_env05_llave_with_an_unknown_number_fails(monkeypatch) -> None:
    monkeypatch.setenv("PAYMENT_NEQUI_NUMBER", "3001112233")
    t = traj(T(1, sent=["Transfiere a la llave 3009998877 y me mandas el comprobante"]))
    r = CODE_CHECKS["ENV-05"](t, CheckContext())
    assert (r.verdict, r.turn) == ("falla", 1)


def test_env05_the_business_nequi_key_is_public_and_allowed(monkeypatch) -> None:
    """Primer informe (9-15 sep): ENV-05 reprobó al bot por escribir la llave
    Nequi del negocio. Es dato PÚBLICO y el único dato de pago que el guion le
    permite escribir (`sales/config/payments.py`, skills de cierre y catálogo).
    Una cuenta bancaria o cualquier otro número sigue siendo falla."""
    monkeypatch.setenv("PAYMENT_NEQUI_NUMBER", "3001112233")
    ok = traj(T(1, sent=["Contra entrega, pago anticipado por Nequi o llave 300 111 2233, o link de pago"]))
    assert CODE_CHECKS["ENV-05"](ok, CheckContext()).verdict == "pasa"
    both = traj(T(1, sent=["Nequi o llave 3001112233, o a la cuenta de ahorros 123456789"]))
    r = CODE_CHECKS["ENV-05"](both, CheckContext())
    assert r.verdict == "falla" and "123456789" in r.evidence


def test_env05_price_next_to_payment_medium_is_not_bank_data() -> None:
    t = traj(T(1, sent=["El total es $89.000 y puedes pagarlo por Nequi o contra entrega"]))
    assert CODE_CHECKS["ENV-05"](t, CheckContext()).verdict == "pasa"
