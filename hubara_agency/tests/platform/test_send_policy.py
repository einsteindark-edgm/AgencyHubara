"""Tests de la central de decisión de envío (`send_policy.evaluate_send`).

Asierta la MATRIZ de costo por envío (`WHATSAPP_WINDOW_STRATEGY.md` §3), que
cruza estado de ventana × canal × category × rate card. Comportamiento
observable: dada una geometría de ventana y una intención de envío, la central
devuelve allowed/channel/category/is_free/expected_cost correctos.

Ver `src/platform/whatsapp/send_policy.py`.
"""
from __future__ import annotations

import pytest

from src.platform.whatsapp.cost import RateCard, RateCardEntry
from src.platform.whatsapp.send_policy import (
    CATEGORY_MARKETING,
    CATEGORY_SERVICE,
    CATEGORY_UTILITY,
    CHANNEL_BLOCKED,
    CHANNEL_FREE_FORM,
    CHANNEL_TEMPLATE,
    evaluate_send,
)


# =============================================================================
# Anchors + fixtures
# =============================================================================

NOW_MS = 1_716_700_000_000
ONE_HOUR_MS = 60 * 60 * 1000


@pytest.fixture
def rate_pre_oct() -> RateCard:
    """Colombia pre-1-oct: service gratis dentro de la ventana."""
    return RateCard(
        version="co_2026q2_v1",
        effective_from_ms=1_717_200_000_000,
        country="CO",
        currency="USD",
        rates={
            "marketing": RateCardEntry(usd_micros_per_message=12500),
            "utility": RateCardEntry(usd_micros_per_message=800),
            "authentication": RateCardEntry(usd_micros_per_message=800),
            "service": RateCardEntry(usd_micros_per_message=0),
        },
    )


@pytest.fixture
def rate_post_oct() -> RateCard:
    """Colombia post-1-oct: service y utility en CSW ya se cobran."""
    return RateCard(
        version="co_2026q4_v1",
        effective_from_ms=1_759_276_800_000,  # 2026-10-01
        country="CO",
        currency="USD",
        rates={
            "marketing": RateCardEntry(usd_micros_per_message=12500),
            "utility": RateCardEntry(usd_micros_per_message=800),
            "authentication": RateCardEntry(usd_micros_per_message=800),
            "service": RateCardEntry(usd_micros_per_message=800),
        },
    )


def _meta(*, csw: bool, ctwa: bool) -> dict:
    """Metadata con las ventanas abiertas/cerradas en NOW_MS."""
    m: dict = {}
    if csw:
        m["service_window_expires_at_ms"] = NOW_MS + ONE_HOUR_MS
    else:
        m["service_window_expires_at_ms"] = NOW_MS - ONE_HOUR_MS
    if ctwa:
        m["ctwa_window_expires_at_ms"] = NOW_MS + ONE_HOUR_MS
    else:
        m["ctwa_window_expires_at_ms"] = NOW_MS - ONE_HOUR_MS
    return m


# =============================================================================
# Fila 1 de la matriz — dentro de 72h CTWA: TODO gratis
# =============================================================================


class TestInsideCtwaWindow:
    def test_free_form_inside_both_windows_is_free_service(self, rate_post_oct):
        d = evaluate_send(
            NOW_MS, _meta(csw=True, ctwa=True),
            CHANNEL_FREE_FORM, CATEGORY_SERVICE, rate_post_oct,
        )
        assert d.allowed is True
        assert d.channel == CHANNEL_FREE_FORM
        assert d.recommended_category == CATEGORY_SERVICE
        assert d.is_free is True
        assert d.expected_cost_micros == 0

    def test_marketing_template_inside_ctwa_is_free(self, rate_post_oct):
        # La 72h CTWA hace gratis INCLUSO un marketing template.
        d = evaluate_send(
            NOW_MS, _meta(csw=False, ctwa=True),
            CHANNEL_TEMPLATE, CATEGORY_MARKETING, rate_post_oct,
        )
        assert d.allowed is True
        assert d.channel == CHANNEL_TEMPLATE
        assert d.recommended_category == CATEGORY_MARKETING
        assert d.is_free is True
        assert d.expected_cost_micros == 0


# =============================================================================
# Fila 2 — en 24h, fuera de 72h: free-form/utility barato, marketing caro
# =============================================================================


class TestInsideCswOnly:
    def test_free_form_service_post_oct_is_charged(self, rate_post_oct):
        d = evaluate_send(
            NOW_MS, _meta(csw=True, ctwa=False),
            CHANNEL_FREE_FORM, CATEGORY_SERVICE, rate_post_oct,
        )
        assert d.allowed is True
        assert d.channel == CHANNEL_FREE_FORM
        assert d.is_free is False
        assert d.expected_cost_micros == 800

    def test_free_form_service_pre_oct_is_free(self, rate_pre_oct):
        d = evaluate_send(
            NOW_MS, _meta(csw=True, ctwa=False),
            CHANNEL_FREE_FORM, CATEGORY_SERVICE, rate_pre_oct,
        )
        assert d.allowed is True
        assert d.is_free is True
        assert d.expected_cost_micros == 0

    def test_marketing_template_in_csw_is_charged_full(self, rate_post_oct):
        d = evaluate_send(
            NOW_MS, _meta(csw=True, ctwa=False),
            CHANNEL_TEMPLATE, CATEGORY_MARKETING, rate_post_oct,
        )
        assert d.allowed is True
        assert d.is_free is False
        assert d.expected_cost_micros == 12500


# =============================================================================
# Fila 3 — fuera de ambas: free-form BLOQUEADO; templates cobran
# =============================================================================


class TestOutsideAllWindows:
    def test_free_form_outside_csw_is_blocked(self, rate_post_oct):
        d = evaluate_send(
            NOW_MS, _meta(csw=False, ctwa=False),
            CHANNEL_FREE_FORM, CATEGORY_SERVICE, rate_post_oct,
        )
        assert d.allowed is False
        assert d.channel == CHANNEL_BLOCKED
        assert d.suppress_reason is not None
        assert d.expected_cost_micros == 0

    def test_utility_template_outside_windows_is_cheap(self, rate_post_oct):
        d = evaluate_send(
            NOW_MS, _meta(csw=False, ctwa=False),
            CHANNEL_TEMPLATE, CATEGORY_UTILITY, rate_post_oct,
        )
        assert d.allowed is True
        assert d.channel == CHANNEL_TEMPLATE
        assert d.recommended_category == CATEGORY_UTILITY
        assert d.is_free is False
        assert d.expected_cost_micros == 800

    def test_marketing_template_outside_windows_is_full_price(self, rate_post_oct):
        d = evaluate_send(
            NOW_MS, _meta(csw=False, ctwa=False),
            CHANNEL_TEMPLATE, CATEGORY_MARKETING, rate_post_oct,
        )
        assert d.allowed is True
        assert d.expected_cost_micros == 12500
