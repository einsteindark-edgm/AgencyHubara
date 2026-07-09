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

import time
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.plugins.ads.meta.client import MetaAdsPort
from src.plugins.ads.meta.composition import get_ads_port, get_token_store
from src.plugins.ads.meta.settings import MetaSettings, meta_settings
from src.plugins.ads.meta.token_store import MetaTokenStorePort

router = APIRouter()


def _settings() -> MetaSettings:
    return meta_settings()


def _store() -> MetaTokenStorePort:
    return get_token_store()


def _ads() -> MetaAdsPort:
    return get_ads_port()


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


@router.get("/analysis-input")
def meta_analysis_input(days: int = 14) -> dict:
    """Arma el JSON que el pod `ads-analytics` de GraphAgents consume, con datos REALES
    de Graph (lo que el botón "Analizar con IA" usa en vez del seed de ejemplo). El pod
    YA parsea este shape — no requiere cambios en GraphAgents.

    `manual_sales` queda vacío (lo completa el operador: ventas WhatsApp no trackeadas
    por Meta). `entities_payload` va vacío (las entities del MCP están gateadas; la señal
    CTWA real vive en `meta_insights.actions`)."""
    token = _store().load()
    if token is None or not token.account_id:
        raise HTTPException(status_code=409, detail="Meta no conectado")

    until_d = date.today()
    since_d = until_d - timedelta(days=max(1, days))
    ads = _ads()

    currency = "COP"
    for acct in ads.list_ad_accounts(token.access_token):
        if acct.account_id == token.account_id:
            currency = acct.currency or "COP"
            break

    meta_insights = ads.fetch_raw_insights(
        token.access_token,
        token.account_id,
        since=since_d.isoformat(),
        until=until_d.isoformat(),
        currency=currency,
    )
    return {
        "meta_insights": meta_insights,
        "manual_sales": {"sales": []},
        "entities_payload": {"ad_entities": "[]", "summary": {"total_count": 0}},
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
