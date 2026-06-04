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

Performance: los 3 endpoints derivan TODO de un mismo scan del vault
(`scan_ad_sessions` lee+parsea cada `wa_*/metadata.json` una vez). El conteo
de mensajes del historial JSONL es lazy (solo se lee para conversaciones aún
activas). Para que una page-view del dashboard (campañas + conversaciones +
serie diaria de la campaña seleccionada) NO dispare 3 scans completos, el scan
se cachea en proceso con un TTL corto y los 3 endpoints lo comparten. El cache
vive acá (capa API) — NO en el use case, que sigue puro (R-STATELESS): con
`sessions=None` escanea fresco, y así lo hacen los tests.
"""
from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path as FsPath
from typing import Any

from fastapi import APIRouter, Path, Query

from src.platform.config import WORKSPACE_VAULT_DIR
from src.plugins.chats.agent.sales.use_cases.list_ads_campaigns import (
    list_ads_campaigns,
    list_attributed_conversations,
    list_daily_series,
    scan_ad_sessions,
)

router = APIRouter()

# TTL del scan cacheado del vault. 15s es invisible para un dashboard de
# analytics (la data de ventas/chats no cambia segundo a segundo) y colapsa los
# 3 scans por page-view en uno, dejando las re-vistas (cambiar de campaña,
# refetch de TanStack) instantáneas. Subirlo reduce I/O a costa de frescura.
_SCAN_TTL_S = 15.0
_DAY_MS = 24 * 60 * 60 * 1000
_scan_cache: dict[str, tuple[float, list[tuple[FsPath, dict[str, Any]]]]] = {}


def _since_ms(days: int | None) -> int | None:
    """Epoch ms del inicio de la ventana (`now - days`), o None para 'todo'.

    El filtro por fecha se empuja al backend: con una ventana acotada, el scan
    saltea (vía mtime) las sesiones sin actividad reciente y la agregación solo
    procesa los episodios en rango → el cómputo escala con la ventana, no con
    todo el historial.
    """
    if not days:
        return None
    return int(time.time() * 1000) - days * _DAY_MS


def _cached_sessions(days: int | None) -> list[tuple[FsPath, dict[str, Any]]]:
    """Scan del vault cacheado por TTL + ventana, compartido por los 3 endpoints.

    Cache-miss → un scan O(sesiones en ventana) (con skip por mtime). Cache-hit
    → O(1). Keyed por (vault, `days`) — estable durante el TTL aunque `since_ms`
    se mueva; los 3 endpoints de una page-view usan el mismo `days` → comparten
    el scan.
    """
    key = f"{WORKSPACE_VAULT_DIR}|{days if days else 'all'}"
    now = time.monotonic()
    hit = _scan_cache.get(key)
    if hit is not None and (now - hit[0]) < _SCAN_TTL_S:
        return hit[1]
    data = scan_ad_sessions(WORKSPACE_VAULT_DIR, since_ms=_since_ms(days))
    _scan_cache[key] = (now, data)
    return data


@router.get("/ads/campaigns")
def get_ads_campaigns(
    days: int | None = Query(
        None, ge=1, le=365, description="ventana en días; omitir = todo el historial"
    ),
) -> dict:
    """Lista de campañas detectadas en el vault.

    Cada campaña se infiere de las sesiones WhatsApp cuyo `origin.channel`
    es `ad`, `post` o `web_referral`. Se agrupa por `origin.source_id`.

    `days` acota la ventana: solo episodios iniciados en los últimos `days`
    días entran a la agregación (revenue/costo LLM/counts reflejan la ventana).
    Omitirlo agrega todo el historial.

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
    campaigns = list_ads_campaigns(
        WORKSPACE_VAULT_DIR, sessions=_cached_sessions(days), since_ms=_since_ms(days)
    )
    return {"campaigns": [asdict(c) for c in campaigns]}


@router.get("/ads/campaigns/{campaign_id}/conversations")
def get_ads_campaign_conversations(
    campaign_id: str = Path(..., description="source_id de la campaña"),
    days: int | None = Query(
        None, ge=1, le=365, description="ventana en días; omitir = todo el historial"
    ),
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
            "value": null,        # pendiente de integrar orders
            "llm_cost_usd": 0.00178,  # costo LLM del episodio (USD, congelado)
            "llm_tokens": 1930        # tokens totales del episodio
          },
          ...
        ]
      }

    Si el `campaign_id` no existe, devuelve `conversations: []`. El
    frontend muestra el empty state existente.
    """
    convs = list_attributed_conversations(
        WORKSPACE_VAULT_DIR,
        campaign_id,
        sessions=_cached_sessions(days),
        since_ms=_since_ms(days),
    )
    return {
        "campaign_id": campaign_id,
        "conversations": [asdict(c) for c in convs],
    }


@router.get("/ads/campaigns/{campaign_id}/daily")
def get_ads_campaign_daily(
    campaign_id: str = Path(..., description="source_id de la campaña"),
    days: int = Query(14, ge=1, le=90, description="ventana en días (default 14)"),
) -> dict:
    """Serie diaria de conversaciones de una campaña (chats iniciados por día).

    Cada día cuenta los episodios cuyo `started_at_ms` cae en ese día
    calendario (America/Bogota), segmentados por estado actual. Serie
    CONTINUA de los últimos `days` días terminando hoy — los días sin
    actividad vienen con counts en 0.

    Response shape:
      {
        "campaign_id": "AD_123",
        "days": 14,
        "series": [
          {"d": "21 may", "ganado": 2, "cotizado": 3, "calificado": 4,
           "activo": 5, "nuevo": 2, "no_reply": 8, "perdido": 1},
          ...
        ]
      }

    Si el `campaign_id` no existe, la serie viene toda en 0 (no rompe).
    """
    points = list_daily_series(
        WORKSPACE_VAULT_DIR, campaign_id, days=days, sessions=_cached_sessions(days)
    )
    return {
        "campaign_id": campaign_id,
        "days": days,
        "series": [asdict(p) for p in points],
    }
