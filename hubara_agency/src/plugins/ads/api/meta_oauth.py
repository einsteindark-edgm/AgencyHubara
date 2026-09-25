"""Sub-router PROTEGIDO `/api/ads/meta/*` — datos + gestión (single-tenant).

- `GET  /api/ads/meta/status`     → estado de conexión (cuenta, scopes, expiración).
- `GET  /api/ads/meta/insights`   → métricas reales por campaña (Marketing API).
- `POST /api/ads/meta/campaigns/{id}/status` → pausa/activa (gestión, requiere ads_management).

Single-tenant (decisión 2026-07-09): el token es un system-user PROVISIONADO por
el operador en SSM `/hubara/<tenant>/meta/oauth` (runbook
`infra/whatsapp-provisioning/README.md`). No hay login/callback OAuth ni
disconnect — conectar/rotar es una operación de infra, no un endpoint.

Estas rutas las dispara el dashboard (con bearer de Cognito en prod) → quedan
protegidas por `require_auth`. Token server-side, nunca logueado.

Providers `_settings/_store/_ads` a nivel módulo para que los tests los monkeypatcheen.
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.plugins.ads.meta.client import MetaAdsPort
from src.plugins.ads.meta.composition import get_ads_port, get_token_store
from src.plugins.ads.meta.settings import MetaSettings, meta_settings
from src.plugins.ads.meta.token_store import MetaTokenStorePort

router = APIRouter()

_log = logging.getLogger(__name__)


def _settings() -> MetaSettings:
    return meta_settings()


def _store() -> MetaTokenStorePort:
    return get_token_store()


def _ads() -> MetaAdsPort:
    return get_ads_port()


def _orders():
    """OrderQueryPort (Medusa = la verdad del pago) — superficie del SDK
    (connectorkit, import perezoso: no carga el vendor si nadie analiza).
    Provider a nivel módulo para que los tests lo monkeypatcheen."""
    from src.sdk.connectorkit import get_order_query_port

    return get_order_query_port()


async def _fetch_paid_sales(since: str, until: str) -> dict:
    """`manual_sales` REAL para el pod: órdenes pagadas de Medusa agregadas por
    día (`sales_join`). Pagina el port completo (cap defensivo). Medusa caída →
    `{"sales": []}` — el análisis degrada honesto, nunca 500 al operador."""
    from src.plugins.ads.sales_join import manual_sales_from_orders

    try:
        port = _orders()
        orders: list = []
        offset = 0
        for _ in range(20):  # cap: 20 páginas × 100 = 2000 órdenes por análisis
            page = await port.list(limit=100, offset=offset, include_drafts=False)
            batch = list(page.orders or [])
            orders.extend(batch)
            if len(batch) < 100:
                break
            offset += 100
        return manual_sales_from_orders(orders, since=since, until=until)
    except Exception:  # noqa: BLE001 — degradar, no tumbar el análisis
        _log.exception("analysis-input: no pude traer las ventas de Medusa — degrado a []")
        return {"sales": []}


@router.get("/status")
def meta_status() -> dict:
    token = _store().load()
    if token is None:
        return {"connected": False}
    # `expired` deriva de la expiración del token (long-lived ~60d). Si expiró, la UI
    # ofrece reconectar en vez de mostrar errores opacos de Graph (premortem #5).
    expired = token.expires_at is not None and token.expires_at < int(time.time())
    return {
        "connected": True,
        "account_id": token.account_id,
        "account_name": token.account_name,
        "scopes": list(token.scopes),
        "expires_at": token.expires_at,
        "expired": expired,
        "can_manage": "ads_management" in token.scopes,
    }


@router.get("/insights")
def meta_insights(days: int = 30, since: str = "", until: str = "") -> dict:
    """Métricas REALES por campaña del Marketing API (lo que llena los campos hoy en `null`).

    Sin conexión → `{connected: false, campaigns: []}` (el dashboard degrada limpio).
    """
    token = _store().load()
    if token is None or not token.account_id:
        return {"connected": False, "campaigns": []}

    if not (since and until):
        until_d = date.today()
        since_d = until_d - timedelta(days=max(1, days))
        since, until = since_d.isoformat(), until_d.isoformat()

    ads = _ads()
    metrics = ads.fetch_campaign_metrics(
        token.access_token, token.account_id, since=since, until=until
    )
    meta_by_id = {c.campaign_id: c for c in ads.list_campaigns(token.access_token, token.account_id)}
    campaigns = [
        {
            "campaign_id": m.campaign_id,
            "name": m.campaign_name,
            "status": meta_by_id[m.campaign_id].status if m.campaign_id in meta_by_id else None,
            "objective": (
                meta_by_id[m.campaign_id].objective if m.campaign_id in meta_by_id else None
            ),
            "spend": m.spend,
            "impressions": m.impressions,
            "reach": m.reach,
            "clicks": m.clicks,
            "messaging_conversations_started": m.messaging_conversations_started,
        }
        for m in metrics
    ]
    return {
        "connected": True,
        "account_id": token.account_id,
        "account_name": token.account_name,
        "since": since,
        "until": until,
        "campaigns": campaigns,
    }


def _campaign_conversations(
    ad_ids: frozenset[str], since: str, until: str
) -> tuple[list, bool]:
    """Chats atribuidos a los anuncios de la campaña en la ventana (fechas Bogotá,
    inclusive), con el estado + monto de su pedido leídos de Orders. SYNC: corre en
    un worker thread (`_order_facts` cruza al event loop con `anyio.from_thread`).
    Provider a nivel módulo para que los tests lo monkeypatcheen. → (chats, stale)."""
    from src.plugins.ads import api as ads_api
    from src.plugins.ads.aggregation import list_attributed_conversations

    since_ms, until_ms = ads_api._window(None, since, until)
    sessions = ads_api._cached_sessions(since_ms)
    facts = ads_api._order_facts(sessions)
    convs = list_attributed_conversations(
        ads_api.WORKSPACE_VAULT_DIR,
        "",
        sessions=sessions,
        since_ms=since_ms,
        until_ms=until_ms,
        source_ids=ad_ids,
        order_facts=facts,
    )
    return convs, facts.stale


def _fill_days(sales: dict, dates: list[str]) -> dict:
    """Días con gasto en Meta y sin ventas = fila en CERO explícita. Sin esto el pod
    los excluía del cálculo y el retorno salía inflado (Halloween: 9 de 15 días)."""
    by_day = {s["date"]: s for s in sales.get("sales", [])}
    for d in dates:
        by_day.setdefault(d, {"date": d, "total_orders": 0, "total_revenue": 0})
    return {"sales": [by_day[d] for d in sorted(by_day)]}


@router.get("/analysis-input")
async def meta_analysis_input(
    days: int = 14,
    campaign_id: str | None = None,
    frm: str | None = Query(None, alias="from"),
    to: str | None = None,
) -> dict:
    """Arma el JSON que el pod `ads-analytics` de GraphAgents consume, con datos REALES
    de Graph (lo que el botón "Analizar con IA" usa en vez del seed de ejemplo).

    Con `campaign_id` (el botón lo manda con la campaña abierta — rediseño 2026-09-25):
    SOLO esa campaña — `meta_insights` filtrado, `manual_sales` = ventas CONFIRMADAS
    de SUS chats por día (Bogotá) y `campaign_breakdown` = el drill-down segmento →
    anuncio → chats que consume `ctwa-scorecard`. Sin `campaign_id`: la cuenta
    completa con las órdenes pagadas de la tienda (caso 424d6647) y
    `campaign_breakdown: null`. En ambos, los días con gasto y sin ventas van en cero.
    Ventana: `from`+`to` (YYYY-MM-DD, inclusive) o los últimos `days` días.
    `entities_payload` va vacío (las entities del MCP están gateadas; la señal CTWA
    real vive en `meta_insights.actions`)."""
    from anyio import to_thread

    from src.plugins.ads.aggregation import _bogota_date
    from src.plugins.ads.analysis_seed import attributed_daily_sales, build_campaign_breakdown

    token = _store().load()
    if token is None or not token.account_id:
        raise HTTPException(status_code=409, detail="Meta no conectado")

    if frm and to:
        since, until = sorted((frm, to))
    else:
        until_d = _bogota_date(int(time.time() * 1000))
        since, until = (until_d - timedelta(days=max(1, days))).isoformat(), until_d.isoformat()
    ads = _ads()

    currency = "COP"
    for acct in ads.list_ad_accounts(token.access_token):
        if acct.account_id == token.account_id:
            currency = acct.currency or "COP"
            break

    meta_insights = ads.fetch_raw_insights(
        token.access_token, token.account_id, since=since, until=until, currency=currency
    )
    if campaign_id:
        meta_insights = {
            **meta_insights,
            "data": [r for r in meta_insights.get("data", []) if str(r.get("campaign_id")) == campaign_id],
        }
    meta_days = sorted({r["date_start"] for r in meta_insights.get("data", []) if r.get("date_start")})

    breakdown = None
    if campaign_id:
        kw = {"campaign_id": campaign_id, "since": since, "until": until}
        period = ads.fetch_campaign_ad_insights(token.access_token, token.account_id, daily=False, **kw)
        daily = ads.fetch_campaign_ad_insights(token.access_token, token.account_id, daily=True, **kw)
        budget_level = ads.fetch_campaign_budget_level(token.access_token, campaign_id)
        ad_ids = frozenset(str(r["ad_id"]) for r in period + daily if r.get("ad_id"))
        convs, stale = await to_thread.run_sync(_campaign_conversations, ad_ids, since, until)
        name = next((r.get("campaign_name") for r in meta_insights.get("data", []) if r.get("campaign_name")), "")
        manual_sales = attributed_daily_sales(convs, dates=meta_days)
        breakdown = build_campaign_breakdown(
            campaign_id=campaign_id, campaign_name=name or campaign_id, budget_level=budget_level,
            since=since, until=until, period_rows=period, daily_rows=daily,
            conversations=convs, orders_stale=stale,
        )
    else:
        manual_sales = _fill_days(await _fetch_paid_sales(since, until), meta_days)
    return {
        "meta_insights": meta_insights,
        "manual_sales": manual_sales,
        "entities_payload": {"ad_entities": "[]", "summary": {"total_count": 0}},
        "campaign_breakdown": breakdown,
    }


class CampaignStatusBody(BaseModel):
    status: str


_VALID_STATUSES = {"PAUSED", "ACTIVE"}


@router.post("/campaigns/{campaign_id}/status")
def meta_set_campaign_status(campaign_id: str, body: CampaignStatusBody) -> dict:
    """Pausa/activa una campaña (gestión). Acción OUTWARD sobre Meta — el HITL es el
    confirm de dos pasos en la UI; acá validamos status + conexión + scope y delegamos
    al port (idempotente)."""
    status = body.status.upper()
    if status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail="status debe ser PAUSED o ACTIVE")
    token = _store().load()
    if token is None:
        raise HTTPException(status_code=409, detail="Meta no conectado")
    if "ads_management" not in token.scopes:
        raise HTTPException(
            status_code=422, detail="el token no tiene scope ads_management (reconectá con gestión)"
        )
    ok = _ads().update_campaign_status(token.access_token, campaign_id, status)
    if not ok:
        raise HTTPException(status_code=502, detail="Meta no confirmó el cambio de estado")
    return {"ok": ok, "campaign_id": campaign_id, "status": status}
