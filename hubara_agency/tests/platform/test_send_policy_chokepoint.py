"""WS2 — la central como choke point del gasto (path de template).

Dos guardas:
  1. `annotate_last_outbound_policy` estampa la estimación de la central en la
     metadata del outbound (la info de costo/lane SIEMPRE sale de la central).
  2. Gate de arquitectura: el path de envío de templates (`activities.py`)
     consulta `send_policy` — sin bypass silencioso.

Ver `WHATSAPP_WINDOW_STRATEGY.md` §6.
"""
from __future__ import annotations

from pathlib import Path

from src.platform.whatsapp.cost import RateCard, RateCardEntry
from src.platform.whatsapp.send_policy import (
    CATEGORY_MARKETING,
    CHANNEL_TEMPLATE,
    annotate_last_outbound_policy,
    evaluate_send,
)

NOW_MS = 1_716_700_000_000
ONE_HOUR_MS = 60 * 60 * 1000


def _rate() -> RateCard:
    return RateCard(
        version="co_2026q4_v1",
        effective_from_ms=1_759_276_800_000,
        country="CO",
        currency="USD",
        rates={
            "marketing": RateCardEntry(usd_micros_per_message=12500),
            "utility": RateCardEntry(usd_micros_per_message=800),
            "service": RateCardEntry(usd_micros_per_message=800),
        },
    )


class TestAnnotateOutboundPolicy:
    def test_stamps_central_estimate_paid_marketing(self):
        meta: dict = {
            "service_window_expires_at_ms": NOW_MS - ONE_HOUR_MS,
            "ctwa_window_expires_at_ms": NOW_MS - ONE_HOUR_MS,
        }
        decision = evaluate_send(
            NOW_MS, meta, CHANNEL_TEMPLATE, CATEGORY_MARKETING, _rate()
        )
        annotate_last_outbound_policy(meta, decision)
        stamp = meta["last_outbound_policy"]
        assert stamp["channel"] == CHANNEL_TEMPLATE
        assert stamp["category"] == CATEGORY_MARKETING
        assert stamp["is_free"] is False
        assert stamp["expected_cost_micros"] == 12500
        assert "rationale" in stamp

    def test_stamps_free_when_inside_ctwa(self):
        meta: dict = {
            "service_window_expires_at_ms": NOW_MS - ONE_HOUR_MS,
            "ctwa_window_expires_at_ms": NOW_MS + ONE_HOUR_MS,  # 72h abierta
        }
        decision = evaluate_send(
            NOW_MS, meta, CHANNEL_TEMPLATE, CATEGORY_MARKETING, _rate()
        )
        annotate_last_outbound_policy(meta, decision)
        assert meta["last_outbound_policy"]["is_free"] is True
        assert meta["last_outbound_policy"]["expected_cost_micros"] == 0


class TestChokePointGate:
    """El path de envío de templates DEBE consultar la central. Grep sobre el
    source — sin bypass silencioso del cost/lane estimate."""

    def test_activities_consult_send_policy(self):
        src = Path(__file__).resolve().parents[2] / (
            "src/platform/whatsapp/activities.py"
        )
        text = src.read_text(encoding="utf-8")
        assert "send_policy" in text, (
            "activities.py debe consultar la central send_policy (choke point)"
        )
        assert "annotate_last_outbound_policy" in text
