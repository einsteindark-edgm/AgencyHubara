"""HTTP endpoints del plugin `ads` (frontend) — leyendo del plugin `chats`.

El plugin `ads` no tiene backend propio: las campañas se derivan del estado
clasificado por el ingest de WhatsApp (`origin` + `last_touch` + referrals
en `wa_*/metadata.json` del vault). Por eso el endpoint vive dentro del
plugin `chats` — donde están los metadata files — y respeta R-DIP (un
plugin frontend NO importa código backend del plugin sibling).

Endpoints:
  GET /api/chats/ads/campaigns
       → lista de campañas detectadas (agrupadas por source_id).
  GET /api/chats/ads/campaigns/{campaign_id}/conversations
       → conversaciones WhatsApp atribuidas a esa campaña.

Datos faltantes (spend, revenue, status Meta, etc.) se devuelven como
`null` — el frontend marca esos slots con un visual marker ("—" + icono
muted) para que el operador sepa qué falta integrar.
"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Path

from src.platform.config import WORKSPACE_VAULT_DIR
from src.plugins.chats.agent.sales.use_cases.list_ads_campaigns import (
    list_ads_campaigns,
    list_attributed_conversations,
)

router = APIRouter()


@router.get("/ads/campaigns")
def get_ads_campaigns() -> dict:
    """Lista de campañas detectadas en el vault.

    Cada campaña se infiere de las sesiones WhatsApp cuyo `origin.channel`
    es `ad`, `post` o `web_referral`. Se agrupa por `origin.source_id`.

    Response shape:
      {
        "campaigns": [
          {
            "id": "AD_123",
            "name": "Velas Hubara",
            "source_type": "ad",
            "started": 3,
            "first_seen_ms": 1714312400000,
            "last_seen_ms": 1714312600000,
            "spend": null,        # pendiente de integrar Meta Ads API
            "revenue": null,      # pendiente de integrar orders
            "status": null,
            ...
          },
          ...
        ]
      }
    """
    campaigns = list_ads_campaigns(WORKSPACE_VAULT_DIR)
    return {"campaigns": [asdict(c) for c in campaigns]}


@router.get("/ads/campaigns/{campaign_id}/conversations")
def get_ads_campaign_conversations(
    campaign_id: str = Path(..., description="source_id de la campaña"),
) -> dict:
    """Conversaciones WhatsApp atribuidas a una campaña.

    Filtra sesiones cuyo `origin.source_id == campaign_id`. Ordenadas
    por `started_at_ms` descendente (más recientes primero).

    Response shape:
      {
        "campaign_id": "AD_123",
        "conversations": [
          {
            "id": "wa_5491111111111",
            "phone_number": "5491111111111",
            "started_at_ms": 1714312400000,
            "last_msg_at_ms": 1714400000000,
            "msgs_count": 14,
            "ad_headline": "Velas Hubara",
            "agent": "ventas",
            "name": null,         # pendiente de integrar CRM
            "city": null,
            "state": null,        # pendiente de clasificador conversacional
            "value": null         # pendiente de integrar orders
          },
          ...
        ]
      }

    Si el `campaign_id` no existe, devuelve `conversations: []`. El
    frontend muestra el empty state existente.
    """
    convs = list_attributed_conversations(WORKSPACE_VAULT_DIR, campaign_id)
    return {
        "campaign_id": campaign_id,
        "conversations": [asdict(c) for c in convs],
    }
