"""Tests del cost helper de WhatsApp Cloud API.

Cubre:
  * compute_message_cost_micros — todos los pricing_types + missing category.
  * add_outbound_to_summary — agregado inicial (billable / free / pending).
  * materialize_pending_in_summary — resolución de pending al llegar webhook.
  * load_rate_card_from_yaml — carga del YAML real Colombia Q2 2026.

Ver `src/platform/whatsapp/cost.py` y HU-WA24H-001 §3.4.
"""
from __future__ import annotations

import pytest

from src.platform.whatsapp.cost import (
    CategoryCostBreakdown,
    OutboundLogEntry,
    PricingSnapshot,
    RateCard,
    RateCardEntry,
    add_outbound_to_summary,
    compute_message_cost_micros,
    empty_episode_cost_summary,
    load_rate_card_from_yaml,
    materialize_pending_in_summary,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def co_q2_2026() -> RateCard:
    """Rate card Colombia abril 2026 — coincide con el YAML committed."""
    return RateCard(
        version="co_2026q2_v1",
        effective_from_ms=1_717_200_000_000,
        country="CO",
        currency="USD",
        rates={
            "marketing": RateCardEntry(usd_micros_per_message=12500),
            "utility": RateCardEntry(usd_micros_per_message=800),
            "authentication": RateCardEntry(usd_micros_per_message=800),
            "authentication_international": RateCardEntry(usd_micros_per_message=None),
            "service": RateCardEntry(usd_micros_per_message=0),
        },
    )


# =============================================================================
# compute_message_cost_micros
# =============================================================================


class TestComputeMessageCostCents:
    def test_non_billable_is_zero(self, co_q2_2026: RateCard):
        pricing = PricingSnapshot(
            billable=False, pricing_type="regular", category="marketing"
        )
        assert compute_message_cost_micros(pricing, co_q2_2026) == 0

    def test_free_customer_service_is_zero(self, co_q2_2026: RateCard):
        pricing = PricingSnapshot(
            billable=True,
            pricing_type="free_customer_service",
            category="utility",
        )
        assert compute_message_cost_micros(pricing, co_q2_2026) == 0

    def test_free_entry_point_is_zero(self, co_q2_2026: RateCard):
        # CTWA dentro de 72h — cualquier category gratis, incluso marketing.
        pricing = PricingSnapshot(
            billable=True,
            pricing_type="free_entry_point",
            category="marketing",
        )
        assert compute_message_cost_micros(pricing, co_q2_2026) == 0

    def test_regular_marketing_charges_full_rate(self, co_q2_2026: RateCard):
        pricing = PricingSnapshot(
            billable=True, pricing_type="regular", category="marketing"
        )
        assert compute_message_cost_micros(pricing, co_q2_2026) == 12500

    def test_regular_utility_charges_full_rate(self, co_q2_2026: RateCard):
        pricing = PricingSnapshot(
            billable=True, pricing_type="regular", category="utility"
        )
        assert compute_message_cost_micros(pricing, co_q2_2026) == 800

    def test_regular_authentication_charges_full_rate(self, co_q2_2026: RateCard):
        pricing = PricingSnapshot(
            billable=True, pricing_type="regular", category="authentication"
        )
        assert compute_message_cost_micros(pricing, co_q2_2026) == 800

    def test_unpriced_category_returns_zero(self, co_q2_2026: RateCard):
        # authentication_international tiene None en Colombia Q2.
        pricing = PricingSnapshot(
            billable=True,
            pricing_type="regular",
            category="authentication_international",
        )
        assert compute_message_cost_micros(pricing, co_q2_2026) == 0

    def test_missing_category_returns_zero(self, co_q2_2026: RateCard):
        # Defensivo: si Meta agrega una category nueva y nuestro rate card
        # no la tiene, retornamos 0 + warning. Mejor que crash.
        pricing = PricingSnapshot(
            billable=True, pricing_type="regular", category="brand_new_category"
        )
        assert compute_message_cost_micros(pricing, co_q2_2026) == 0


# =============================================================================
# add_outbound_to_summary
# =============================================================================


class TestAddOutboundToSummary:
    def test_pending_increments_pending_count_only(self):
        summary = empty_episode_cost_summary()
        log_entry = OutboundLogEntry(
            sent_at_ms=1000,
            wa_message_id="wamid.A",
            kind="template",
            template_name="quote_ready_utility_v1",
            pricing=None,
            cost_usd_micros=None,
            rate_card_version=None,
        )

        result = add_outbound_to_summary(summary, log_entry)

        assert result.messages_count == 1
        assert result.messages_pending_count == 1
        assert result.messages_billable_count == 0
        assert result.messages_free_count == 0
        assert result.total_usd_micros == 0
        assert result.by_category == {}
        assert result.by_pricing_type == {}

    def test_free_message_increments_free_count_and_breakdown(self):
        summary = empty_episode_cost_summary()
        pricing = PricingSnapshot(
            billable=True,
            pricing_type="free_customer_service",
            category="utility",
        )
        log_entry = OutboundLogEntry(
            sent_at_ms=1000,
            wa_message_id="wamid.A",
            kind="text",
            template_name=None,
            pricing=pricing,
            cost_usd_micros=0,
            rate_card_version="co_2026q2_v1",
        )

        result = add_outbound_to_summary(summary, log_entry)

        assert result.messages_count == 1
        assert result.messages_free_count == 1
        assert result.messages_billable_count == 0
        assert result.messages_pending_count == 0
        assert result.total_usd_micros == 0
        # Count en breakdown sube pero cents queda en 0.
        assert result.by_category["utility"] == CategoryCostBreakdown(count=1, usd_micros=0)
        assert result.by_pricing_type["free_customer_service"] == CategoryCostBreakdown(
            count=1, usd_micros=0
        )

    def test_billable_message_increments_total_and_breakdowns(self):
        summary = empty_episode_cost_summary()
        pricing = PricingSnapshot(
            billable=True, pricing_type="regular", category="marketing"
        )
        log_entry = OutboundLogEntry(
            sent_at_ms=1000,
            wa_message_id="wamid.A",
            kind="template",
            template_name="cart_recovery_marketing_v1",
            pricing=pricing,
            cost_usd_micros=12500,
            rate_card_version="co_2026q2_v1",
        )

        result = add_outbound_to_summary(summary, log_entry)

        assert result.messages_count == 1
        assert result.messages_billable_count == 1
        assert result.total_usd_micros == 12500
        assert result.by_category["marketing"] == CategoryCostBreakdown(
            count=1, usd_micros=12500
        )
        assert result.by_pricing_type["regular"] == CategoryCostBreakdown(
            count=1, usd_micros=12500
        )

    def test_mixed_sequence_maintains_invariants(self):
        """4 outbounds: 1 pending, 1 free, 2 billable (marketing + utility)."""
        summary = empty_episode_cost_summary()
        # 1. pending
        summary = add_outbound_to_summary(
            summary,
            OutboundLogEntry(
                sent_at_ms=1,
                wa_message_id="A",
                kind="text",
                template_name=None,
                pricing=None,
                cost_usd_micros=None,
                rate_card_version=None,
            ),
        )
        # 2. free (utility en ventana)
        summary = add_outbound_to_summary(
            summary,
            OutboundLogEntry(
                sent_at_ms=2,
                wa_message_id="B",
                kind="template",
                template_name="quote_ready_utility_v1",
                pricing=PricingSnapshot(
                    billable=True,
                    pricing_type="free_customer_service",
                    category="utility",
                ),
                cost_usd_micros=0,
                rate_card_version="co_2026q2_v1",
            ),
        )
        # 3. billable marketing
        summary = add_outbound_to_summary(
            summary,
            OutboundLogEntry(
                sent_at_ms=3,
                wa_message_id="C",
                kind="template",
                template_name="cart_recovery_marketing_v1",
                pricing=PricingSnapshot(
                    billable=True, pricing_type="regular", category="marketing"
                ),
                cost_usd_micros=12500,
                rate_card_version="co_2026q2_v1",
            ),
        )
        # 4. billable utility (fuera ventana)
        summary = add_outbound_to_summary(
            summary,
            OutboundLogEntry(
                sent_at_ms=4,
                wa_message_id="D",
                kind="template",
                template_name="payment_pending_utility_v1",
                pricing=PricingSnapshot(
                    billable=True, pricing_type="regular", category="utility"
                ),
                cost_usd_micros=800,
                rate_card_version="co_2026q2_v1",
            ),
        )

        assert summary.messages_count == 4
        assert summary.messages_pending_count == 1
        assert summary.messages_free_count == 1
        assert summary.messages_billable_count == 2
        assert summary.total_usd_micros == 13300

        # Invariante: total == sum(by_category[*].usd_micros)
        total_from_cat = sum(b.usd_micros for b in summary.by_category.values())
        assert total_from_cat == summary.total_usd_micros

        # Invariante: total == sum(by_pricing_type[*].usd_micros)
        total_from_type = sum(
            b.usd_micros for b in summary.by_pricing_type.values()
        )
        assert total_from_type == summary.total_usd_micros

        # by_category solo tiene entradas para outbounds con pricing
        # (3: 1 free utility + 1 marketing + 1 billable utility = 2 en utility, 1 en marketing).
        assert summary.by_category["utility"] == CategoryCostBreakdown(
            count=2, usd_micros=800
        )
        assert summary.by_category["marketing"] == CategoryCostBreakdown(
            count=1, usd_micros=12500
        )


# =============================================================================
# materialize_pending_in_summary
# =============================================================================


class TestMaterializePendingInSummary:
    def test_pending_resolves_to_billable(self):
        summary = empty_episode_cost_summary()
        # 1. Insert pending
        summary = add_outbound_to_summary(
            summary,
            OutboundLogEntry(
                sent_at_ms=1,
                wa_message_id="A",
                kind="template",
                template_name="quote",
                pricing=None,
                cost_usd_micros=None,
                rate_card_version=None,
            ),
        )
        assert summary.messages_pending_count == 1
        assert summary.messages_billable_count == 0

        # 2. Webhook llega — log_entry re-builded con cost
        resolved = OutboundLogEntry(
            sent_at_ms=1,
            wa_message_id="A",
            kind="template",
            template_name="quote",
            pricing=PricingSnapshot(
                billable=True, pricing_type="regular", category="utility"
            ),
            cost_usd_micros=800,
            rate_card_version="co_2026q2_v1",
        )
        summary = materialize_pending_in_summary(summary, resolved)

        assert summary.messages_pending_count == 0
        assert summary.messages_billable_count == 1
        assert summary.total_usd_micros == 800
        assert summary.by_category["utility"] == CategoryCostBreakdown(
            count=1, usd_micros=800
        )

    def test_pending_resolves_to_free(self):
        summary = empty_episode_cost_summary()
        summary = add_outbound_to_summary(
            summary,
            OutboundLogEntry(
                sent_at_ms=1,
                wa_message_id="A",
                kind="text",
                template_name=None,
                pricing=None,
                cost_usd_micros=None,
                rate_card_version=None,
            ),
        )

        resolved = OutboundLogEntry(
            sent_at_ms=1,
            wa_message_id="A",
            kind="text",
            template_name=None,
            pricing=PricingSnapshot(
                billable=True,
                pricing_type="free_customer_service",
                category="service",
            ),
            cost_usd_micros=0,
            rate_card_version="co_2026q2_v1",
        )
        summary = materialize_pending_in_summary(summary, resolved)

        assert summary.messages_pending_count == 0
        assert summary.messages_free_count == 1
        assert summary.total_usd_micros == 0

    def test_idempotent_when_pending_already_zero(self):
        """Defensa: webhook duplicado (Meta a veces reenvía) NO debe
        producir messages_pending_count negativo."""
        summary = empty_episode_cost_summary()
        # No hay nada pendiente.

        resolved = OutboundLogEntry(
            sent_at_ms=1,
            wa_message_id="A",
            kind="text",
            template_name=None,
            pricing=PricingSnapshot(
                billable=True, pricing_type="regular", category="utility"
            ),
            cost_usd_micros=800,
            rate_card_version="co_2026q2_v1",
        )
        summary = materialize_pending_in_summary(summary, resolved)

        # pending no baja a -1 (clamp en 0).
        assert summary.messages_pending_count == 0
        # Pero el total y billable count sí suben (es un mensaje real).
        assert summary.messages_billable_count == 1
        assert summary.total_usd_micros == 800

    def test_unresolved_log_entry_does_not_corrupt_summary(self):
        """Si por error el caller pasa un log_entry con cost None,
        retornar summary intacto en vez de corromper invariantes."""
        summary = empty_episode_cost_summary()
        summary = add_outbound_to_summary(
            summary,
            OutboundLogEntry(
                sent_at_ms=1,
                wa_message_id="A",
                kind="text",
                template_name=None,
                pricing=None,
                cost_usd_micros=None,
                rate_card_version=None,
            ),
        )
        before = summary

        unresolved = OutboundLogEntry(
            sent_at_ms=1,
            wa_message_id="A",
            kind="text",
            template_name=None,
            pricing=None,
            cost_usd_micros=None,
            rate_card_version=None,
        )
        after = materialize_pending_in_summary(summary, unresolved)

        assert after == before


# =============================================================================
# load_rate_card_from_yaml — carga del YAML real committed
# =============================================================================


class TestLoadRateCardFromYaml:
    def test_loads_co_2026q2_v1(self):
        rate_card = load_rate_card_from_yaml("co_2026q2_v1")

        assert rate_card.version == "co_2026q2_v1"
        assert rate_card.country == "CO"
        assert rate_card.currency == "USD"
        assert rate_card.rates["marketing"].usd_micros_per_message == 12500
        assert rate_card.rates["utility"].usd_micros_per_message == 800
        assert rate_card.rates["authentication"].usd_micros_per_message == 800
        assert (
            rate_card.rates["authentication_international"].usd_micros_per_message is None
        )
        assert rate_card.rates["service"].usd_micros_per_message == 0

    def test_raises_when_version_not_found(self):
        with pytest.raises(FileNotFoundError, match="Rate card YAML not found"):
            load_rate_card_from_yaml("nonexistent_version_999")

    def test_integration_compute_cost_with_real_yaml(self):
        """E2E: rate card real cargado del YAML + compute_message_cost_micros."""
        rate_card = load_rate_card_from_yaml("co_2026q2_v1")
        pricing = PricingSnapshot(
            billable=True, pricing_type="regular", category="marketing"
        )
        assert compute_message_cost_micros(pricing, rate_card) == 12500
