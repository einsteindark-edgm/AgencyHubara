"""HTTP endpoints del plugin `ads` — backend self-contained.

El plugin `ads` deriva las campañas del estado clasificado por el ingest de
WhatsApp (`origin` + `last_touch` + referrals en `wa_*/metadata.json` del
vault). Lee el vault vía `platform` (`WORKSPACE_VAULT_DIR`); la agregación vive
en `src.plugins.ads.aggregation` y la clasificación en
`src.plugins.ads.classification` — internas al plugin (extraído de chats,
PLUGIN_CONTRACT.md §5.2). Self-contained: no importa de ningún plugin sibling.

Endpoints (prefix `/api/ads` del manifest):
  GET /api/ads/campaigns
       → lista de campañas detectadas (agrupadas por source_id).
  GET /api/ads/campaigns/{campaign_id}/conversations
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

from src.plugins.ads.aggregation import (
    bogota_day_start_ms,
    list_ads_campaigns,
    list_attributed_conversations,
    list_daily_series,
    scan_ad_sessions,
)
from src.sdk.runtime import WORKSPACE_VAULT_DIR

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


def _window(
    days: int | None, frm: str | None, to: str | None
) -> tuple[int | None, int | None]:
    """`(since_ms, until_ms)` de la ventana de la UI.

    - Rango custom (`from`+`to`, YYYY-MM-DD, ambos inclusive): gana sobre `days`.
      `until_ms` es EXCLUSIVO (medianoche del día siguiente a `to`) para incluir
      todo el día `to`. Orden tolerante (si `from` > `to` se intercambian). Fecha
      inválida → ventana abierta (degradación leniente, no 422).
    - Preset (`days`): `since = now − days`, `until = None` (hasta ahora).
    """
    if frm and to:
        lo, hi = (frm, to) if frm <= to else (to, frm)
        since = bogota_day_start_ms(lo)
        hi_start = bogota_day_start_ms(hi)
        if since is None or hi_start is None:
            return None, None
        return since, hi_start + _DAY_MS
    return _since_ms(days), None


def _cached_sessions(since_ms: int | None) -> list[tuple[FsPath, dict[str, Any]]]:
    """Scan del vault cacheado por TTL + `since_ms`, compartido por los 3 endpoints.

    El scan SOLO depende de `since_ms` (pre-filtro por mtime; el `until_ms` del
    rango custom se aplica per-episodio en cada use case). Por eso la key es
    `since_ms`: los 3 endpoints de una page-view (preset o rango custom) comparten
    el mismo `since` → un solo scan. Cache-miss → scan O(sesiones en ventana) (con
    skip por mtime); cache-hit → O(1).
    """
    key = f"{WORKSPACE_VAULT_DIR}|{since_ms if since_ms is not None else 'all'}"
    now = time.monotonic()
    hit = _scan_cache.get(key)
    if hit is not None and (now - hit[0]) < _SCAN_TTL_S:
        return hit[1]
    data = scan_ad_sessions(WORKSPACE_VAULT_DIR, since_ms=since_ms)
    _scan_cache[key] = (now, data)
    return data


@router.get("/campaigns")
def get_ads_campaigns(
    days: int | None = Query(
        None, ge=1, le=365, description="ventana en días; omitir = todo el historial"
    ),
    frm: str | None = Query(
        None,
        alias="from",
        description="YYYY-MM-DD inicio (inclusive); con `to` activa rango custom y anula `days`",
    ),
    to: str | None = Query(
        None, description="YYYY-MM-DD fin (inclusive); requiere `from`"
    ),
) -> dict:
    """Lista de campañas detectadas en el vault.

    Cada campaña se infiere de las sesiones WhatsApp cuyo `origin.channel`
    es `ad`, `post` o `web_referral`. Se agrupa por `origin.source_id`.

    Filtro por fecha: `days` acota la ventana relativa (últimos N días); o bien
    `from`+`to` (YYYY-MM-DD, ambos inclusive) para un rango exacto que gana sobre
    `days`. Solo episodios iniciados en la ventana entran a la agregación
    (revenue/costo LLM/counts la reflejan). Sin ninguno → todo el historial.

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
    since_ms, until_ms = _window(days, frm, to)
    campaigns = list_ads_campaigns(
        WORKSPACE_VAULT_DIR,
        sessions=_cached_sessions(since_ms),
        since_ms=since_ms,
        until_ms=until_ms,
    )
    return {"campaigns": [asdict(c) for c in campaigns]}


@router.get("/campaigns/{campaign_id}/conversations")
def get_ads_campaign_conversations(
    campaign_id: str = Path(..., description="source_id de la campaña"),
    days: int | None = Query(
        None, ge=1, le=365, description="ventana en días; omitir = todo el historial"
    ),
    frm: str | None = Query(
        None,
        alias="from",
        description="YYYY-MM-DD inicio (inclusive); con `to` activa rango custom y anula `days`",
    ),
    to: str | None = Query(
        None, description="YYYY-MM-DD fin (inclusive); requiere `from`"
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
    since_ms, until_ms = _window(days, frm, to)
    convs = list_attributed_conversations(
        WORKSPACE_VAULT_DIR,
        campaign_id,
        sessions=_cached_sessions(since_ms),
        since_ms=since_ms,
        until_ms=until_ms,
    )
    return {
        "campaign_id": campaign_id,
        "conversations": [asdict(c) for c in convs],
    }


@router.get("/campaigns/{campaign_id}/daily")
def get_ads_campaign_daily(
    campaign_id: str = Path(..., description="source_id de la campaña"),
    days: int = Query(14, ge=1, le=90, description="ventana en días (default 14)"),
    frm: str | None = Query(
        None,
        alias="from",
        description="YYYY-MM-DD inicio (inclusive); con `to` activa rango custom y anula `days`",
    ),
    to: str | None = Query(
        None, description="YYYY-MM-DD fin (inclusive); requiere `from`"
    ),
) -> dict:
    """Serie diaria de conversaciones de una campaña (chats iniciados por día).

    Cada día cuenta los episodios cuyo `started_at_ms` cae en ese día
    calendario (America/Bogota), segmentados por estado actual. Serie CONTINUA
    (días sin actividad en 0). La ventana es los últimos `days` días terminando
    hoy, o el rango `from`+`to` (YYYY-MM-DD inclusive) si se proveen — clampeada
    a 90 columnas. `days` del response es la longitud real de la serie.

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
    if frm and to:
        since_ms, until_ms = _window(None, frm, to)
        scan_since = since_ms
    else:
        since_ms = until_ms = None
        scan_since = _since_ms(days)
    points = list_daily_series(
        WORKSPACE_VAULT_DIR,
        campaign_id,
        days=days,
        since_ms=since_ms,
        until_ms=until_ms,
        sessions=_cached_sessions(scan_since),
    )
    return {
        "campaign_id": campaign_id,
        "days": len(points),
        "series": [asdict(p) for p in points],
    }
