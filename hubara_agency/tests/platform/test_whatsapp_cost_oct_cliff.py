"""Guards del acantilado de pricing del 1-oct-2026.

Dos cosas:
  1. El rate card `co_2026q4_v1` existe y cobra service + utility (ya NO son
     gratis dentro de la CSW desde el 1-oct-2026).
  2. RIESGO CONOCIDO documentado: `compute_message_cost_micros` hace
     short-circuit en `pricing_type in FREE_PRICING_TYPES` ANTES de leer el
     rate card. Si Meta sigue mandando `free_customer_service` para service
     messages in-window mientras EMPIEZA a cobrarlos (1-oct), subcontamos el
     gasto en silencio. Este guard PINea el comportamiento actual para que el
     cambio sea deliberado y visible, no un descubrimiento en producción.
     Revisión atada a la tarea de verificación contra webhook real.

Ver `WHATSAPP_WINDOW_STRATEGY.md` §10 y `cost.py:147`.
"""
from __future__ import annotations

from src.platform.whatsapp.cost import (
    PricingSnapshot,
    compute_message_cost_micros,
    load_rate_card_from_yaml,
)


class TestOctoberRateCard:
    def test_q4_card_charges_service_within_window(self):
        card = load_rate_card_from_yaml("co_2026q4_v1")
        # Post-1-oct: service message ya se cobra (era 0 en Q2).
        assert card.rates["service"].usd_micros_per_message == 800

    def test_q4_card_charges_utility(self):
        card = load_rate_card_from_yaml("co_2026q4_v1")
        # Utility dentro de CSW deja de ser gratis el 1-oct.
        assert card.rates["utility"].usd_micros_per_message == 800

    def test_q4_card_effective_oct_1(self):
        card = load_rate_card_from_yaml("co_2026q4_v1")
        # 2026-10-01T00:00:00Z.
        assert card.rates["marketing"].usd_micros_per_message == 12500
        assert card.effective_from_ms == 1_759_276_800_000


class TestSilentUndercountRisk:
    """PIN del riesgo: `free_customer_service` cortocircuita a 0 aunque el rate
    card cobre `service`. Si este test cambia de comportamiento, es PORQUE
    verificamos el shape real del webhook post-oct y ajustamos deliberadamente.
    """

    def test_free_customer_service_shortcircuits_to_zero_even_when_card_charges(
        self,
    ):
        card = load_rate_card_from_yaml("co_2026q4_v1")  # service = 800
        snapshot = PricingSnapshot(
            billable=True,
            pricing_type="free_customer_service",
            category="service",
        )
        cost = compute_message_cost_micros(snapshot, card)
        # 🔴 RIESGO: da 0 aunque el card cobra 800. Autoritativo = webhook real
        # (tarea de verificación). NO "arreglar" a ciegas — depende de qué
        # pricing_type manda Meta cuando empieza a cobrar.
        assert cost == 0

    def test_regular_service_uses_card_rate(self):
        # Si Meta manda `regular` + category `service` (hipótesis probable
        # post-oct), el cost SÍ fluye del card. Este es el camino sano.
        card = load_rate_card_from_yaml("co_2026q4_v1")
        snapshot = PricingSnapshot(
            billable=True, pricing_type="regular", category="service"
        )
        assert compute_message_cost_micros(snapshot, card) == 800
