"""Tests de la capa funnel `decide_reengagement` (Free-First Funnel).

Asierta la política de reactivación (`WHATSAPP_WINDOW_STRATEGY.md` §4):
  * Fase A (dentro de 72h CTWA / 24h CSW) → reactivar GRATIS.
  * Fase B (ambas cerradas) → frío = suprimir; gancho transaccional = utility;
    alto-valor con opt-in = un marketing.

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
    LeadState,
    decide_reengagement,
)

NOW_MS = 1_716_700_000_000
ONE_HOUR_MS = 60 * 60 * 1000


@pytest.fixture
def rate() -> RateCard:
    return RateCard(
        version="co_2026q4_v1",
        effective_from_ms=1_759_276_800_000,
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
    off = NOW_MS - ONE_HOUR_MS
    on = NOW_MS + ONE_HOUR_MS
    return {
        "service_window_expires_at_ms": on if csw else off,
        "ctwa_window_expires_at_ms": on if ctwa else off,
    }


# =============================================================================
# Estados terminales → suprimir (no tocar)
# =============================================================================


class TestTerminalStates:
    def test_human_owned_is_suppressed(self, rate):
        d = decide_reengagement(
            NOW_MS, _meta(csw=True, ctwa=True), LeadState(tag="HUMANO"), rate
        )
        assert d.allowed is False
        assert d.suppress_reason == "human_owned"

    def test_already_converted_is_suppressed(self, rate):
        d = decide_reengagement(
            NOW_MS, _meta(csw=True, ctwa=True), LeadState(tag="COMPRA_EXITOSA"), rate
        )
        assert d.allowed is False
        assert d.suppress_reason == "already_converted"

    def test_compra_hecha_suprime_aunque_el_tag_corriente_cambie(self, rate):
        """Bug del scheduler post-venta (2026-07-17): al devolver la
        conversación a sales el tag flipa COMPRA_EXITOSA→RETOMA_VENTA y el
        terminal por tag corriente deja de ver la compra — con
        has_registered_order=True el 'gancho transaccional' dispararía un
        utility a quien YA compró. El cierre del último episodio
        (last_closing_tag) manda: compra hecha → suprimir."""
        lead = LeadState(
            tag="RETOMA_VENTA",
            has_registered_order=True,
            last_closing_tag="COMPRA_EXITOSA",
        )
        d = decide_reengagement(NOW_MS, _meta(csw=False, ctwa=True), lead, rate)
        assert d.allowed is False
        assert d.suppress_reason == "already_purchased"

    def test_tag_remarketing_explicito_levanta_la_supresion_de_compra(self, rate):
        """Excepción deliberada: el humano devolvió la conversación a
        REMARKETING a propósito (botón del dashboard escribe tag=REMARKETING
        antes de arrancar el workflow) — esa decisión humana manda sobre la
        supresión por compra hecha."""
        lead = LeadState(
            tag="REMARKETING",
            has_registered_order=True,
            last_closing_tag="COMPRA_EXITOSA",
        )
        d = decide_reengagement(NOW_MS, _meta(csw=False, ctwa=True), lead, rate)
        assert d.allowed is True

    def test_episodio_nuevo_sin_cierre_no_hereda_la_supresion(self, rate):
        """El próximo inbound abre episodio nuevo (closing_tag=None): la
        supresión por compra se auto-levanta — cliente activo de nuevo."""
        lead = LeadState(
            tag="INTERESADO",
            has_order_draft=True,
            last_closing_tag=None,
        )
        d = decide_reengagement(NOW_MS, _meta(csw=False, ctwa=True), lead, rate)
        assert d.allowed is True
        assert d.recommended_category == CATEGORY_UTILITY


# =============================================================================
# Fase A — dentro de ventanas: reactivar gratis
# =============================================================================


class TestPhaseAFree:
    def test_csw_open_reengages_via_free_form(self, rate):
        # Cliente activo (respondió hace <24h) → free-form.
        d = decide_reengagement(
            NOW_MS, _meta(csw=True, ctwa=False), LeadState(tag="INTERESADO"), rate
        )
        assert d.allowed is True
        assert d.channel == CHANNEL_FREE_FORM
        assert d.recommended_category == CATEGORY_SERVICE

    def test_ctwa_open_csw_closed_cold_uses_free_marketing_template(self, rate):
        # Dentro de 72h pero CSW cerrada → template (free-form bloqueado), y el
        # marketing template es GRATIS dentro de la 72h.
        d = decide_reengagement(
            NOW_MS,
            _meta(csw=False, ctwa=True),
            LeadState(tag="NO_ETIQUETADO", is_ctwa_lead=True),
            rate,
        )
        assert d.allowed is True
        assert d.channel == CHANNEL_TEMPLATE
        assert d.recommended_category == CATEGORY_MARKETING
        assert d.is_free is True
        assert d.expected_cost_micros == 0

    def test_ctwa_open_with_transactional_hook_prefers_free_utility(self, rate):
        d = decide_reengagement(
            NOW_MS,
            _meta(csw=False, ctwa=True),
            LeadState(tag="INTERESADO", has_order_draft=True, is_ctwa_lead=True),
            rate,
        )
        assert d.allowed is True
        assert d.channel == CHANNEL_TEMPLATE
        assert d.recommended_category == CATEGORY_UTILITY
        assert d.is_free is True


# =============================================================================
# Fase B — ambas cerradas: quirúrgico
# =============================================================================


class TestPhaseB:
    def test_transactional_hook_uses_cheap_utility(self, rate):
        # Pedido a medias fuera de ventanas → utility genuina, barata.
        d = decide_reengagement(
            NOW_MS,
            _meta(csw=False, ctwa=False),
            LeadState(tag="CONFIRMADO_PAGO_PENDIENTE", has_registered_order=True),
            rate,
        )
        assert d.allowed is True
        assert d.channel == CHANNEL_TEMPLATE
        assert d.recommended_category == CATEGORY_UTILITY
        assert d.expected_cost_micros == 800

    def test_cold_lead_no_hook_is_suppressed(self, rate):
        # El instinto del operador: si no se calentó en 72h, no pagar marketing.
        d = decide_reengagement(
            NOW_MS,
            _meta(csw=False, ctwa=False),
            LeadState(tag="NO_ETIQUETADO"),
            rate,
        )
        assert d.allowed is False
        assert d.channel == CHANNEL_BLOCKED
        assert d.suppress_reason == "fase_b_cold_suppressed"
        assert d.expected_cost_micros == 0

    def test_high_value_optin_allows_one_paid_marketing(self, rate):
        # Opt-in explícito para el toque pago (lead de alto valor esperado).
        d = decide_reengagement(
            NOW_MS,
            _meta(csw=False, ctwa=False),
            LeadState(tag="INTERESADO", engaged=True, allow_paid_marketing=True),
            rate,
        )
        assert d.allowed is True
        assert d.channel == CHANNEL_TEMPLATE
        assert d.recommended_category == CATEGORY_MARKETING
        assert d.expected_cost_micros == 12500
