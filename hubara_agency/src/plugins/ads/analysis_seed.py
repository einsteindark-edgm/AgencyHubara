"""Seed del análisis con IA de UNA campaña — capa PURA para el pod `ads-analytics`.

Por qué existe (Halloween, 2026-09-25): el análisis mandaba toda la cuenta de Meta
contra todas las ventas pagadas de la tienda, fechadas en UTC, y el pod botaba los
días sin ventas → retorno 3.05 cuando el real, atribuido y confirmado, era 1.43.

Acá, para una campaña:
  * `attributed_daily_sales` — `manual_sales` con SOLO las ventas confirmadas de SUS
    chats, por día de inicio del chat en Bogotá, y los días sin venta en cero.
  * `build_campaign_breakdown` — el drill-down que consume `ctwa-scorecard`
    (GraphAgents): anuncio → segmento, métricas del periodo + serie diaria de Meta, y
    cada chat con su tarjeta (headline) y el estado + monto de su pedido.

El fetch (Meta, vault, Orders) vive en el endpoint (`api/meta_oauth.py`).
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from src.plugins.ads.aggregation import AdsAttributedConversation, _bogota_date
from src.plugins.ads.meta.parse import _conversations, _num

SCHEMA_VERSION = 1
TIMEZONE = "America/Bogota"
UNKNOWN_ADSET = "Sin segmento (sin datos de Meta)"


def _day(conv: AdsAttributedConversation) -> str:
    return _bogota_date(conv.started_at_ms).isoformat()


def attributed_daily_sales(
    conversations: Iterable[AdsAttributedConversation], *, dates: Iterable[str]
) -> dict:
    """Ventas CONFIRMADAS (`order_status == "paid"`) de los chats de la campaña,
    agregadas por día de inicio del chat (Bogotá). Cada fecha de `dates` (los días
    con datos de Meta) sale aunque no haya venta — en cero, nunca excluida."""
    by_day = {d: {"total_orders": 0, "total_revenue": 0} for d in dates}
    for conv in conversations:
        if conv.order_status != "paid":
            continue
        agg = by_day.setdefault(_day(conv), {"total_orders": 0, "total_revenue": 0})
        agg["total_orders"] += 1
        agg["total_revenue"] += int(conv.order_value_cop or conv.value or 0)
    return {"sales": [{"date": d, **agg} for d, agg in sorted(by_day.items())]}


def _int(value: Any) -> int:
    return int(round(_num(value)))


def _meta(row: dict | None) -> dict:
    row = row or {}
    return {
        "spend_cop": _int(row.get("spend")),
        "impressions": _int(row.get("impressions")),
        "reach": _int(row.get("reach")),
        "link_clicks": _int(row.get("inline_link_clicks")),
        "conversations": _conversations(row.get("actions") or []),
    }


def _chat(conv: AdsAttributedConversation) -> dict:
    order = None
    if conv.order_status:
        order = {"status": conv.order_status, "value_cop": int(conv.order_value_cop or 0)}
    return {"date": _day(conv), "headline": conv.ad_headline or "", "state": conv.state, "order": order}


def build_campaign_breakdown(
    *,
    campaign_id: str,
    campaign_name: str,
    budget_level: str,
    since: str,
    until: str,
    period_rows: list[dict],
    daily_rows: list[dict],
    conversations: list[AdsAttributedConversation],
    orders_stale: bool,
) -> dict:
    """El `campaign_breakdown` del seed (schema 1). Un anuncio con chats pero sin
    fila de Meta (borrado / fuera del insight) entra igual, con gasto 0 conocido:
    sus chats no se pierden."""
    daily: dict[str, list[dict]] = defaultdict(list)
    for row in daily_rows:
        m = _meta(row)
        daily[str(row.get("ad_id"))].append({
            "date": row.get("date_start"), "spend_cop": m["spend_cop"],
            "impressions": m["impressions"], "link_clicks": m["link_clicks"],
            "conversations": m["conversations"],
        })
    chats: dict[str, list[AdsAttributedConversation]] = defaultdict(list)
    for conv in conversations:
        chats[str(conv.source_id)].append(conv)

    def ad(ad_id: str, row: dict | None) -> dict:
        own = sorted(chats.get(ad_id, []), key=lambda c: c.started_at_ms)
        return {
            "ad_id": ad_id,
            "ad_name": (row or {}).get("ad_name") or f"Anuncio {ad_id}",
            "adset_id": (row or {}).get("adset_id"),
            "adset_name": (row or {}).get("adset_name") or UNKNOWN_ADSET,
            "meta": _meta(row),
            "daily": sorted(daily.get(ad_id, []), key=lambda d: d["date"] or ""),
            "chats": [_chat(c) for c in own],
        }

    ads = [ad(str(r.get("ad_id")), r) for r in period_rows]
    known = {a["ad_id"] for a in ads}
    ads += [ad(ad_id, None) for ad_id in sorted(chats) if ad_id not in known]
    return {
        "schema": SCHEMA_VERSION,
        "campaign": {"id": campaign_id, "name": campaign_name, "budget_level": budget_level},
        "window": {"since": since, "until": until, "timezone": TIMEZONE},
        "orders_stale": orders_stale,
        "ads": ads,
    }
