"""Cuándo y por dónde se pide la calificación de un pedido entregado.

Regla del operador (2026-10-13): pedirla antes de que cierre la ventana de 24 h
para aprovecharla (mensaje normal, más barato y sin plantilla); si la ventana
está cerrada, la plantilla de marketing con el descuento sale al día siguiente
de la entrega. Nunca en horario de silencio, nunca dos veces, nunca a pedidos
de prueba.
"""
from __future__ import annotations

from src.plugins.reviews.domain.planning import (
    ReviewRequestFacts,
    ReviewTiming,
    plan_review_request,
)

MIN = 60_000
HOUR = 60 * MIN
T0 = 1_760_000_000_000  # entrega
TIMING = ReviewTiming(
    min_delay_ms=2 * HOUR,
    window_margin_ms=1 * HOUR,
    template_after_ms=20 * HOUR,
    give_up_after_ms=48 * HOUR,
    poll_ms=30 * MIN,
)


def _facts(**overrides) -> ReviewRequestFacts:
    base = dict(
        delivered_at_ms=T0,
        window_expires_at_ms=None,
        quiet_hours_end_ms=None,
        opted_out=False,
        order_ok=True,
        already_requested=False,
        already_reviewed=False,
    )
    base.update(overrides)
    return ReviewRequestFacts(**base)


def test_open_window_after_min_delay_sends_the_flow_in_window():
    step = plan_review_request(T0 + 3 * HOUR, _facts(window_expires_at_ms=T0 + 10 * HOUR), TIMING)
    assert step.action == "send_flow"


def test_open_window_before_min_delay_waits_until_the_delay():
    step = plan_review_request(T0 + 30 * MIN, _facts(window_expires_at_ms=T0 + 20 * HOUR), TIMING)
    assert step.action == "wait"
    assert step.wait_ms == 90 * MIN


def test_window_about_to_close_sends_even_before_min_delay():
    # Ventana cierra en 50 min: si esperamos el retraso mínimo la perdemos.
    now = T0 + 30 * MIN
    step = plan_review_request(now, _facts(window_expires_at_ms=now + 50 * MIN), TIMING)
    assert step.action == "send_flow"


def test_wait_never_overshoots_the_window_margin():
    # Retraso mínimo dice esperar 90 min, pero la ventana cierra en 100 min:
    # hay que despertar antes del margen (100 - 60 = 40 min).
    now = T0 + 30 * MIN
    step = plan_review_request(now, _facts(window_expires_at_ms=now + 100 * MIN), TIMING)
    assert step.action == "wait"
    assert step.wait_ms == 40 * MIN


def test_closed_window_waits_polling_in_case_the_customer_writes():
    step = plan_review_request(T0 + 3 * HOUR, _facts(), TIMING)
    assert step.action == "wait"
    assert step.wait_ms == 30 * MIN


def test_closed_window_poll_does_not_overshoot_the_template_time():
    step = plan_review_request(T0 + 20 * HOUR - 10 * MIN, _facts(), TIMING)
    assert step.action == "wait"
    assert step.wait_ms == 10 * MIN


def test_closed_window_after_template_time_sends_the_template():
    step = plan_review_request(T0 + 20 * HOUR, _facts(window_expires_at_ms=T0 + 1 * HOUR), TIMING)
    assert step.action == "send_template"


def test_marketing_opt_out_skips_the_template():
    step = plan_review_request(T0 + 21 * HOUR, _facts(opted_out=True), TIMING)
    assert (step.action, step.reason) == ("skip", "opted_out")


def test_opt_out_does_not_block_the_in_window_flow():
    # Dentro de la ventana es atención sobre su pedido, no una promoción masiva.
    step = plan_review_request(
        T0 + 3 * HOUR, _facts(opted_out=True, window_expires_at_ms=T0 + 10 * HOUR), TIMING
    )
    assert step.action == "send_flow"


def test_quiet_hours_wait_until_they_end():
    now = T0 + 21 * HOUR
    step = plan_review_request(now, _facts(quiet_hours_end_ms=now + 5 * HOUR), TIMING)
    assert step.action == "wait"
    assert step.wait_ms == 5 * HOUR


def test_too_old_gives_up():
    step = plan_review_request(T0 + 48 * HOUR, _facts(), TIMING)
    assert (step.action, step.reason) == ("skip", "expired")


def test_test_or_not_delivered_order_is_skipped():
    step = plan_review_request(T0 + 3 * HOUR, _facts(order_ok=False), TIMING)
    assert (step.action, step.reason) == ("skip", "order_not_eligible")


def test_already_requested_or_reviewed_is_skipped():
    assert plan_review_request(T0 + 3 * HOUR, _facts(already_requested=True), TIMING).reason == "already_requested"
    assert plan_review_request(T0 + 3 * HOUR, _facts(already_reviewed=True), TIMING).reason == "already_reviewed"
