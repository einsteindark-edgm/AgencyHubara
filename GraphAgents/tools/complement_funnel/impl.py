"""Lógica PURA de `complement-funnel` — G-AGNOSTIC: solo stdlib. EL COMPLEMENTO.

Toma las campañas del MCP `ads_get_ad_entities` (objetivo-aware, pero con la conversación
en 0 para purchase-conversion — brecha de atribución) y las completa con la conversación
REAL de Graph `/insights` `actions` (sumada por `campaign_id`). Así "Día del padre"
(purchase-conversion, entities=0) recupera su conversación.

`conversation_source` deja AUDITABLE de dónde salió cada número (insights / entities /
none) — no inventa. Es la materialización de "lo que el MCP entities no da directamente,
lo completa /insights actions" (ver `docs/meta-ctwa-data-acquisition.md`).
"""
from __future__ import annotations


def _conversations_by_campaign(insights: list) -> dict:
    """Suma la conversación de /insights (por campaña/día) en un total por campaign_id."""
    out: dict = {}
    for row in insights:
        cid = row.get("campaign_id")
        if cid is None:
            continue
        out[cid] = out.get(cid, 0) + row.get("messaging_conversations_started", 0)
    return out


def run(*, payload: dict) -> dict:
    """payload = {entities: [<parse-meta-entities>], insights: [<meta-ads-insights por campaña/día>]}.

    Devuelve filas por-campaña con la conversación complementada + su fuente.
    """
    entities = payload.get("entities", [])
    insights_conv = _conversations_by_campaign(payload.get("insights", []))

    out = []
    for c in entities:
        cid = c["campaign_id"]
        if cid in insights_conv:
            conversations = insights_conv[cid]
            source = "insights"   # ← el complemento: la señal que el entities NO tenía (purchase-conversion)
        elif c.get("is_messaging"):
            conversations = c.get("result_count", 0)
            source = "entities"   # entities ya la traía (adset optimizado a CONVERSATIONS)
        else:
            conversations = 0
            source = "none"       # sin señal de mensajería en ninguna fuente
        out.append({
            "campaign_id": cid,
            "campaign_name": c.get("campaign_name", ""),
            "objective": c.get("objective", ""),
            "spend_cop": c.get("spend_cop", 0),
            "link_clicks": c.get("link_clicks", 0),
            "conversations": conversations,
            "conversation_source": source,
            # reclasificación: si /insights confirma mensajería, ES messaging aunque el
            # entities la haya clasificado por su objetivo de compra.
            "is_messaging": bool(c.get("is_messaging")) or source == "insights",
        })
    return {"campaigns": out}
