"""La configuración de las reseñas sale del entorno (Terraform → SSM → .env).

Nace APAGADA: hasta que el operador la enciende (primero para los números de
prueba), ningún pedido entregado recibe la invitación.
"""
from __future__ import annotations

from src.plugins.reviews.agent.settings import ReviewSettings


def test_defaults_are_off_and_the_agreed_rewards():
    s = ReviewSettings.from_env({})
    assert s.mode == "off"
    assert s.allows("wa_573001234567") is False
    assert (s.coupon_code_for(5), s.coupon_code_for(10)) == ("GRACIAS5", "GRACIAS10")
    assert s.coupon_days == 60
    # Premortem P12: el botón va directo a Google (no depende de que la web
    # haya publicado /opinar/).
    assert s.google_url == "https://g.page/r/CdsqIGl0gWaWECE/review"
    assert s.timing.min_delay_ms == 2 * 3_600_000
    assert s.timing.template_after_ms == 20 * 3_600_000


def test_test_mode_only_reaches_the_test_numbers():
    s = ReviewSettings.from_env(
        {"REVIEW_REQUESTS_MODE": "test", "REVIEW_TEST_SESSIONS": "wa_573001111111, wa_573000000000"}
    )
    assert s.allows("wa_573001111111") is True
    assert s.allows("wa_573007654321") is False


def test_on_mode_reaches_everyone_and_reads_the_knobs():
    s = ReviewSettings.from_env(
        {
            "REVIEW_REQUESTS_MODE": "on",
            "META_FLOW_ID_REVIEW": "1234",
            "REVIEW_REQUEST_MIN_DELAY_MINUTES": "60",
            "REVIEW_COUPON_DAYS": "30",
            "GOOGLE_REVIEW_URL": "https://g.page/r/X/review",
        }
    )
    assert s.allows("wa_573007654321") is True
    assert s.flow_id == "1234"
    assert s.timing.min_delay_ms == 3_600_000
    assert s.coupon_days == 30
    assert s.google_url == "https://g.page/r/X/review"


def test_an_unknown_mode_or_bad_number_falls_back_to_safe_defaults():
    s = ReviewSettings.from_env({"REVIEW_REQUESTS_MODE": "si", "REVIEW_COUPON_DAYS": "muchos"})
    assert s.mode == "off"
    assert s.coupon_days == 60


def test_ssm_placeholder_flow_id_counts_as_missing():
    s = ReviewSettings.from_env({"META_FLOW_ID_REVIEW": "PLACEHOLDER_set_out_of_band"})
    assert s.flow_id is None


def test_test_numbers_are_accepted_as_the_operator_writes_them():
    """Premortem I4: «+57 300 765 4321» o «573007654321» son el mismo número
    de prueba que la conversación `wa_573007654321`."""
    s = ReviewSettings.from_env(
        {"REVIEW_REQUESTS_MODE": "test", "REVIEW_TEST_SESSIONS": "+57 300 765 4321, 573001111111"}
    )
    assert s.allows("wa_573007654321") is True
    assert s.allows("wa_573001111111") is True


def test_the_worker_says_at_startup_what_is_missing():
    """Premortem I5: al arrancar se ve el modo y qué falta para invitar."""
    s = ReviewSettings.from_env({"REVIEW_REQUESTS_MODE": "test", "REVIEW_TEST_SESSIONS": "wa_573001111111"})
    text = s.describe()
    assert "modo test" in text and "1 número de prueba" in text
    assert "falta META_FLOW_ID_REVIEW" in text
    ready = ReviewSettings.from_env({"REVIEW_REQUESTS_MODE": "on", "META_FLOW_ID_REVIEW": "123"})
    assert "falta" not in ready.describe()
