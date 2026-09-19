"""Un pedido cancelado deja de contar como "ganado" en Ads.

Caso `wa_…__ep_001` (2026-09-18): pedido contra entrega, el operador confirmó
el pago (COMPRA_EXITOSA + Purchase a Meta) y al día siguiente el cliente lo
canceló. Orders lo mostró cancelado y a Meta le llegó `OrderCanceled`, pero Ads
siguió diciendo "ganado" + badge "Purchase": el clasificador miraba solo la
copia del chat (`order_id` / `closing_tag`), que la cancelación no toca.

La etapa del pedido se lee de `OrderFacts` (gotcha 13), igual que el revenue:

  * pedido cancelado en Orders → "perdido" + `state_reason="order_cancelled"`;
  * Medusa no responde por ese pedido → se conserva el estado del chat;
  * `OrderCanceled` enviado a Meta → es el badge CAPI (pisa a Purchase).
"""
from __future__ import annotations

import json
from pathlib import Path

from src.plugins.ads.aggregation import (
    list_ads_campaigns,
    list_attributed_conversations,
    list_daily_series,
)
from src.plugins.ads.classification import classify_episode_state
from src.sdk.connectorkit import OrderFacts, OrderFactsSnapshot

_DAY_MS = 24 * 60 * 60 * 1000
_NOW_MS = 1_789_700_000_000
_STARTED_MS = _NOW_MS - _DAY_MS
ORDER = "order_77"


def _fact(*, pay: str = "paid", stage: str = "cancelled") -> OrderFacts:
    return OrderFacts(
        order_id=ORDER, display_id="#77", total_cop=49500, currency_code="cop",
        pay_status=pay, stage=stage, customer="Ana", is_draft=False,
    )


def _cancelled() -> OrderFactsSnapshot:
    return OrderFactsSnapshot(facts={ORDER: _fact()})


def _capi(event_name: str, event_id: str) -> dict:
    return {"event_id": event_id, "event_name": event_name, "status": "sent", "http_status": 200}


def _session(
    vault: Path,
    *,
    closing_tag: str = "COMPRA_EXITOSA",
    capi_events: list[dict] | None = None,
    legacy: bool = False,
) -> None:
    d = vault / "wa_573001234567"
    d.mkdir(parents=True, exist_ok=True)
    md: dict = {
        "origin": {
            "channel": "ad", "first_seen_ms": _STARTED_MS, "headline": "Velas", "source_id": "AD_X",
        },
        "active_route": "humano",
        "tag": "HUMANO",
        "capi_events_sent": capi_events or [],
    }
    if legacy:
        md["tag"] = closing_tag
        md["registered_order"] = {"order_id": ORDER, "total_cop": 49500, "success": True}
    else:
        md["episodes"] = [
            {
                "episode_id": "ep_001",
                "started_at_ms": _STARTED_MS,
                "closed_at_ms": _STARTED_MS + 1,
                "closing_tag": closing_tag,
                "order_id": ORDER,
                "order_total_cop": 49500,
                "referral_snapshot": None,
            }
        ]
    (d / "metadata.json").write_text(json.dumps(md), encoding="utf-8")


def _conv(vault: Path, facts: OrderFactsSnapshot | None):
    (conv,) = list_attributed_conversations(vault, "AD_X", order_facts=facts)
    return conv


# ── clasificador puro ───────────────────────────────────────────────────────


def test_classifier_cancelled_order_is_perdido_even_with_order_id_and_compra_exitosa() -> None:
    ep = {"order_id": ORDER, "closing_tag": "COMPRA_EXITOSA", "closed_at_ms": 5}
    state = classify_episode_state(
        ep, current_tag=None, total_msgs=0, last_inbound_ms=None, now_ms=_NOW_MS,
        order_cancelled=True,
    )
    assert state == "perdido"


# ── tabla de conversaciones ─────────────────────────────────────────────────


def test_conversation_with_cancelled_order_is_perdido_with_reason(tmp_path: Path) -> None:
    _session(tmp_path)
    conv = _conv(tmp_path, _cancelled())
    assert conv.state == "perdido"
    assert conv.state_reason == "order_cancelled"
    assert conv.value is None


def test_pending_payment_order_cancelled_is_perdido(tmp_path: Path) -> None:
    _session(tmp_path, closing_tag="CONFIRMADO_PAGO_PENDIENTE")
    facts = OrderFactsSnapshot(facts={ORDER: _fact(pay="pending")})
    assert _conv(tmp_path, facts).state == "perdido"


def test_live_order_stays_ganado_without_reason(tmp_path: Path) -> None:
    _session(tmp_path)
    facts = OrderFactsSnapshot(facts={ORDER: _fact(stage="preparing")})
    conv = _conv(tmp_path, facts)
    assert conv.state == "ganado"
    assert conv.state_reason is None


def test_unresolved_order_keeps_the_chat_state(tmp_path: Path) -> None:
    """Medusa caído: sin dato del pedido no se degrada una venta a perdido."""
    _session(tmp_path)
    facts = OrderFactsSnapshot(unresolved=frozenset({ORDER}), stale=True)
    assert _conv(tmp_path, facts).state == "ganado"


def test_legacy_session_with_cancelled_registered_order_is_perdido(tmp_path: Path) -> None:
    _session(tmp_path, legacy=True)
    assert _conv(tmp_path, _cancelled()).state == "perdido"


# ── badge CAPI ──────────────────────────────────────────────────────────────


def test_capi_badge_shows_order_canceled_after_purchase(tmp_path: Path) -> None:
    _session(
        tmp_path,
        capi_events=[
            _capi("Purchase", f"purchase_{ORDER}"),
            _capi("OrderCanceled", f"ordercanceled_{ORDER}"),
        ],
    )
    assert _conv(tmp_path, _cancelled()).capi_event == "OrderCanceled"


def test_capi_badge_stays_purchase_without_order_canceled(tmp_path: Path) -> None:
    _session(tmp_path, capi_events=[_capi("Purchase", f"purchase_{ORDER}")])
    facts = OrderFactsSnapshot(facts={ORDER: _fact(stage="preparing")})
    assert _conv(tmp_path, facts).capi_event == "Purchase"


# ── agregados de campaña + serie diaria ─────────────────────────────────────


def test_campaign_counts_cancelled_order_as_perdido(tmp_path: Path) -> None:
    _session(tmp_path)
    (camp,) = [c for c in list_ads_campaigns(tmp_path, order_facts=_cancelled()) if c.id == "AD_X"]
    assert camp.conversations["ganado"] == 0
    assert camp.conversations["perdido"] == 1
    assert camp.revenue is None


def test_daily_series_counts_cancelled_order_as_perdido(tmp_path: Path) -> None:
    _session(tmp_path)
    points = list_daily_series(
        tmp_path, "AD_X", days=7, now_ms=_NOW_MS, order_facts=_cancelled()
    )
    assert sum(p.ganado for p in points) == 0
    assert sum(p.perdido for p in points) == 1
