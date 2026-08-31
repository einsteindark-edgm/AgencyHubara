"""HTTP endpoints del plugin `ads` — backend self-contained.

El plugin `ads` deriva las campañas del estado clasificado por el ingest de
WhatsApp (`origin` + `last_touch` + referrals en `wa_*/metadata.json` del
vault). Lee el vault vía `platform` (`WORKSPACE_VAULT_DIR`); la agregación vive
en `src.plugins.ads.aggregation` y la clasificación en
`src.plugins.ads.classification` — internas al plugin (extraído de chats,
PLUGIN_CONTRACT.md §5.2). Self-contained: no importa de ningún plugin sibling.

Endpoints (prefix `/api/ads` del manifest):
  GET /api/ads/campaigns
       → lista de campañas (buckets por ad del vault, agrupados por campaña
         Meta vía el resolver ad→{campaña, adset} — segmentación 2026-07-10).
  GET /api/ads/campaigns/{campaign_id}/adsets
       → segmentos (ad sets) de la campaña — drill-down del desplegable.
  GET /api/ads/campaigns/{campaign_id}/conversations[?adset_id=]
       → conversaciones WhatsApp atribuidas a esa campaña (o al segmento).
  GET /api/ads/campaigns/{campaign_id}/daily[?adset_id=]
       → serie diaria (scopeable por segmento).

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
    SYNTHETIC_CAMPAIGN_IDS,
    bogota_day_start_ms,
    list_ads_campaigns,
    list_attributed_conversations,
    list_daily_series,
    scan_ad_sessions,
)
from src.plugins.ads.api.analysis import router as _analysis_router
from src.plugins.ads.api.meta_oauth import router as _meta_router
from src.plugins.ads.meta_merge import merge_meta_campaigns
from src.plugins.ads.meta_names import fetch_meta_ad_names
from src.plugins.ads.segmentation import (
    collect_source_ids,
    group_buckets_by_adset,
    group_buckets_by_campaign,
    merge_meta_adsets,
    scope_source_ids,
)
from src.sdk.runtime import WORKSPACE_VAULT_DIR

router = APIRouter()

# Buzón de análisis con IA (`/api/ads/analysis/*`): dispara el pod `ads-analytics` de
# GraphAgents y relaya el progreso por SSE. Sub-router aparte (runs/orchestrator), montado
# bajo `/analysis` para no colisionar con los endpoints de campañas de abajo.
router.include_router(_analysis_router, prefix="/analysis")

# OAuth + datos de Meta (`/api/ads/meta/*`): botón "Conectar con Meta" (Facebook Login),
# callback, estado de conexión. El cliente Graph que esto autentica alimenta el merge de
# métricas en `/api/ads/campaigns` (abajo).
router.include_router(_meta_router, prefix="/meta")

# TTL del scan cacheado del vault. 15s es invisible para un dashboard de
# analytics (la data de ventas/chats no cambia segundo a segundo) y colapsa los
# 3 scans por page-view en uno, dejando las re-vistas (cambiar de campaña,
# refetch de TanStack) instantáneas. Subirlo reduce I/O a costa de frescura.
_SCAN_TTL_S = 15.0
_DAY_MS = 24 * 60 * 60 * 1000
_scan_cache: dict[str, tuple[float, list[tuple[FsPath, dict[str, Any]]]]] = {}

# Cache del enrichment de nombres Meta (fix 2026-07-01). Los nombres de
# ads/campañas cambian casi nunca — TTL largo (10 min) para no pegarle a
# Graph API en cada page-view. Key = set de ad_ids pedidos; el fetch es
# best-effort ({} en error) y un {} también se cachea (evita martillar a
# Graph cuando está caído — se reintenta recién al expirar el TTL).
_META_NAMES_TTL_S = 600.0
_meta_names_cache: dict[str, tuple[float, dict[str, dict[str, str | None]]]] = {}


def _meta_names_token() -> str:
    """Token para Marketing API (`ads_read`). El System User token del
    tenant ya trae el scope (infra/whatsapp-provisioning/README.md §0).
    Vacío → enrichment off (best-effort, headlines intactos)."""
    import os

    return (
        os.environ.get("META_SYSTEM_USER_TOKEN")
        or os.environ.get("WHATSAPP_ACCESS_TOKEN")
        or ""
    )


def _cached_meta_names(ad_ids: list[str]) -> dict[str, dict[str, str | None]]:
    """`fetch_meta_ad_names` con cache TTL en proceso (capa API, igual que
    el scan del vault — el use case sigue puro)."""
    if not ad_ids:
        return {}
    token = _meta_names_token()
    if not token:
        return {}
    key = ",".join(sorted(ad_ids))
    now = time.monotonic()
    hit = _meta_names_cache.get(key)
    if hit is not None and (now - hit[0]) < _META_NAMES_TTL_S:
        return hit[1]
    names = fetch_meta_ad_names(ad_ids, token=token)
    _meta_names_cache[key] = (now, names)
    return names


#: Campañas Meta (Marketing API) para el merge del listado — cache TTL por ventana.
#: 60s: el spend/estado no cambia más rápido y el listado se pide en cada page-view.
_META_CAMPAIGN_TTL_S = 60.0
_meta_campaign_cache: dict[str, tuple[float, tuple[list, list]]] = {}


def _meta_store():
    """Token store de la conexión Meta (provisionada por infra). Provider a nivel
    módulo para que los tests lo monkeypatcheen (patrón meta_oauth)."""
    from src.plugins.ads.meta.composition import get_token_store

    return get_token_store()


def _meta_ads():
    from src.plugins.ads.meta.composition import get_ads_port

    return get_ads_port()


def _cached_meta_campaigns(
    since_ms: int | None, until_ms: int | None
) -> tuple[list, list]:
    """`(meta_campaigns, metrics)` del Marketing API para la ventana del listado.

    Best-effort como el resto de la capa Meta: sin conexión, Graph caído o
    cualquier excepción → `([], [])` y el listado queda solo-vault (nunca 500).
    Sin ventana ("Total") las métricas se acotan a 90 días — insights necesita
    un rango y el spend histórico completo no aporta al operador.
    """
    from datetime import date, timedelta

    try:
        token = _meta_store().load()
    except Exception:  # noqa: BLE001 — best-effort (SSM caído ≠ dashboard caído)
        return [], []
    if token is None or not token.account_id:
        return [], []

    until_d = date.fromtimestamp(until_ms / 1000) if until_ms else date.today()
    since_d = (
        date.fromtimestamp(since_ms / 1000) if since_ms else until_d - timedelta(days=90)
    )
    key = f"{since_d}|{until_d}"
    now = time.monotonic()
    hit = _meta_campaign_cache.get(key)
    if hit is not None and (now - hit[0]) < _META_CAMPAIGN_TTL_S:
        return hit[1]
    try:
        ads = _meta_ads()
        metas = ads.list_campaigns(token.access_token, token.account_id)
        metrics = ads.fetch_campaign_metrics(
            token.access_token,
            token.account_id,
            since=since_d.isoformat(),
            until=until_d.isoformat(),
        )
    except Exception:  # noqa: BLE001
        return [], []
    _meta_campaign_cache[key] = (now, (metas, metrics))
    return metas, metrics


#: Métricas por adset (insights level=adset) para el drill-down — cache TTL por
#: ventana, mismo criterio que las de campaña (60s).
_meta_adset_cache: dict[str, tuple[float, list]] = {}


def _cached_meta_adsets(since_ms: int | None, until_ms: int | None) -> list:
    """Insights level=adset del Marketing API para la ventana pedida.

    Best-effort como toda la capa Meta: sin conexión / Graph caído → `[]`
    y el drill-down queda solo-vault (nunca 500).
    """
    from datetime import date, timedelta

    try:
        token = _meta_store().load()
    except Exception:  # noqa: BLE001 — best-effort (SSM caído ≠ dashboard caído)
        return []
    if token is None or not token.account_id:
        return []

    until_d = date.fromtimestamp(until_ms / 1000) if until_ms else date.today()
    since_d = (
        date.fromtimestamp(since_ms / 1000) if since_ms else until_d - timedelta(days=90)
    )
    key = f"{since_d}|{until_d}"
    now = time.monotonic()
    hit = _meta_adset_cache.get(key)
    if hit is not None and (now - hit[0]) < _META_CAMPAIGN_TTL_S:
        return hit[1]
    try:
        rows = _meta_ads().fetch_adset_metrics(
            token.access_token,
            token.account_id,
            since=since_d.isoformat(),
            until=until_d.isoformat(),
        )
    except Exception:  # noqa: BLE001
        return []
    _meta_adset_cache[key] = (now, rows)
    return rows


def _scope_names(
    sessions: list[tuple[FsPath, dict[str, Any]]],
) -> dict[str, dict[str, str | None]]:
    """Resolver ad→{campaña, adset} para los ads presentes en el scan.

    Reusa el batch + cache de nombres (`_cached_meta_names`): los endpoints de
    drill-down traducen el id agrupado (campaña/adset) al set de source_ids
    sin re-agregar campañas.
    """
    return _cached_meta_names(sorted(collect_source_ids(sessions)))


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
    # Agrupación jerárquica (segmentación 2026-07-10): los buckets del vault
    # son por AD (source_id del referral); el resolver ad→{campaña, adset}
    # (batch + cache TTL) los agrupa en filas por CAMPAÑA con agregados
    # exactos — incluye el enrichment de nombres reales (fix 2026-07-01).
    # Best-effort: sin token o Graph caído, names={} y cada bucket pasa
    # intacto (una fila por ad con headline, como antes).
    ad_ids = [c.id for c in campaigns if c.id not in SYNTHETIC_CAMPAIGN_IDS]
    if ad_ids:
        campaigns = group_buckets_by_campaign(
            campaigns, _cached_meta_names(ad_ids)
        )
    # Merge de campañas Meta (pedido 2026-07-09): las campañas del Marketing API
    # entran a ESTA lista (seleccionables como las del vault, canvas central
    # incluido) — bucket único matcheado se enriquece; el resto entra standalone.
    metas, metrics = _cached_meta_campaigns(since_ms, until_ms)
    if metas or metrics:
        campaigns = merge_meta_campaigns(campaigns, metas, metrics)
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
    adset_id: str | None = Query(
        None, description="acota el drill-down a un segmento (ad set) de la campaña"
    ),
) -> dict:
    """Conversaciones WhatsApp atribuidas a una campaña (o a un segmento).

    `campaign_id` acepta el id AGRUPADO (campaña Meta) — se traduce al set
    de ads de esa campaña vía el resolver — o un source_id crudo (legacy /
    bucket `direct`). Con `adset_id` el scope se acota al segmento.
    Ordenadas por `started_at_ms` descendente (más recientes primero).

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
    sessions = _cached_sessions(since_ms)
    scope = scope_source_ids(
        _scope_names(sessions), campaign_id=campaign_id, adset_id=adset_id
    )
    convs = list_attributed_conversations(
        WORKSPACE_VAULT_DIR,
        campaign_id,
        sessions=sessions,
        since_ms=since_ms,
        until_ms=until_ms,
        # scope vacío = id sin resolver → id crudo (source_id legacy / direct)
        source_ids=scope or None,
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
    adset_id: str | None = Query(
        None, description="acota la serie a un segmento (ad set) de la campaña"
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
    sessions = _cached_sessions(scan_since)
    scope = scope_source_ids(
        _scope_names(sessions), campaign_id=campaign_id, adset_id=adset_id
    )
    points = list_daily_series(
        WORKSPACE_VAULT_DIR,
        campaign_id,
        days=days,
        since_ms=since_ms,
        until_ms=until_ms,
        sessions=sessions,
        source_ids=scope or None,
    )
    return {
        "campaign_id": campaign_id,
        "days": len(points),
        "series": [asdict(p) for p in points],
    }


@router.get("/campaigns/{campaign_id}/adsets")
def get_ads_campaign_adsets(
    campaign_id: str = Path(..., description="id de la campaña (fila agrupada)"),
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
    """Segmentos (ad sets) de una campaña — el drill-down del desplegable.

    Cada fila reusa el shape de campaña (mismo contrato en el frontend):
    agregados del vault (chats/estados/revenue/LLM/CAPI) agrupando los ads
    del segmento + métricas Meta level=adset (spend/impressions/clicks/conv)
    cuando hay conexión. Segmentos con gasto pero sin chats entran con
    `started=0` (visibilidad de gasto sin resultados). Campaña sin resolver
    (`direct`, legacy) → `ad_sets: []`.

    Response shape: `{"campaign_id": ..., "ad_sets": [<campaign shape>...]}`.
    """
    since_ms, until_ms = _window(days, frm, to)
    sessions = _cached_sessions(since_ms)
    names = _scope_names(sessions)
    scope = scope_source_ids(names, campaign_id=campaign_id)
    rows = []
    if scope:
        buckets = list_ads_campaigns(
            WORKSPACE_VAULT_DIR,
            sessions=sessions,
            since_ms=since_ms,
            until_ms=until_ms,
        )
        members = [b for b in buckets if b.id in scope]
        rows = group_buckets_by_adset(members, names)
    metrics = [
        m for m in _cached_meta_adsets(since_ms, until_ms)
        if m.campaign_id == campaign_id
    ]
    if metrics:
        rows = merge_meta_adsets(rows, metrics)
    return {"campaign_id": campaign_id, "ad_sets": [asdict(r) for r in rows]}
