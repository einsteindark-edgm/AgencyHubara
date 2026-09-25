"""Lógica PURA de `entity-economics` — G-AGNOSTIC: solo stdlib (decimal).

Las métricas de UNA entidad del drill-down (campaña, segmento, anuncio o tarjeta)
desde sus totales crudos. Aritmética EXACTA (Decimal serializado); denominador 0 o
dato ausente → None, JAMÁS un número adivinado. El redondeo es de presentación.

Las ventas son las CONFIRMADAS (pagadas). Pendientes/canceladas no entran: el
scorecard las lleva aparte para no inflar el retorno (caso Halloween 2026-09-25).
"""
from __future__ import annotations

from decimal import Decimal


def _ratio(num: int | None, den: int | None) -> str | None:
    if num is None or not den:
        return None
    return str(Decimal(num) / Decimal(den))


def run(*, payload: dict) -> dict:
    """payload = {spend_cop, impressions, reach|None, link_clicks, conversations, chats,
    paid_sales, paid_revenue_cop} → las métricas (Decimal como string, o None)."""
    spend = payload["spend_cop"]
    sales = payload["paid_sales"]
    revenue = payload["paid_revenue_cop"]
    chats = payload["chats"]
    return {
        "roas": _ratio(revenue, spend),
        "cost_per_sale_cop": _ratio(spend, sales),
        "cost_per_chat_cop": _ratio(spend, chats),
        "chat_to_sale": _ratio(sales, chats),
        "frequency": _ratio(payload["impressions"], payload.get("reach")),
        "ctr": _ratio(payload["link_clicks"], payload["impressions"]),
        "click_to_chat": _ratio(payload["conversations"], payload["link_clicks"]),
        "avg_ticket_cop": _ratio(revenue, sales),
        "ad_share_of_revenue": _ratio(spend, revenue),
    }
