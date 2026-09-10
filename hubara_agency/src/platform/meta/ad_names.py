"""Nombres REALES de ads/campañas de Meta (Marketing API) — resolver único.

Problema (caso real 2026-07-01): el referral CTWA trae el `headline` del ad,
que suele ser el TEXTO DEL CTA ("Chatea con nosotros"), no el nombre de la
campaña en Ads Manager ("Día del Padre"). El operador no reconoce sus
campañas. Los `source_id` del referral son ad ids — Graph los resuelve con UN
GET batch::

    GET {graph_url()}/?ids=<ad_id,...>&fields=name,campaign{id,name},...

Vivía en ``src/plugins/ads/meta_names.py``; se mueve a platform (expuesto por
``src.sdk.connectorkit``) porque el dashboard de Chats también necesita
mostrar el origen real de cada conversación (P-3: chats no importa ads).

Best-effort SIEMPRE: sin token, sin ids, HTTP != 200 o error de red → ``{}``
(el caller degrada al headline, nunca bloquea ni levanta).
"""
from __future__ import annotations

import logging
import os

import httpx

from src.platform.meta.graph import graph_url

logger = logging.getLogger(__name__)

_FIELDS = "name,campaign{id,name},adset{id,name},creative{thumbnail_url}"
_TIMEOUT_S = 4.0


def meta_marketing_token() -> str:
    """Token para Marketing API (`ads_read`). El System User token del tenant
    ya trae el scope (infra/whatsapp-provisioning/README.md §0). Vacío →
    enrichment off (best-effort, headlines intactos)."""
    return (
        os.environ.get("META_SYSTEM_USER_TOKEN")
        or os.environ.get("WHATSAPP_ACCESS_TOKEN")
        or ""
    )


def fetch_meta_ad_names(
    ad_ids: list[str],
    *,
    token: str,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, dict[str, str | None]]:
    """Resuelve `{ad_id: {ad_name, campaign_name, campaign_id, adset_id,
    adset_name, thumbnail_url}}` en un call. Timeout corto (4s) para no colgar
    un endpoint del dashboard si Graph está lento."""
    if not token or not ad_ids:
        return {}
    params = {
        "ids": ",".join(ad_ids),
        "fields": _FIELDS,
        "access_token": token,
    }
    try:
        with httpx.Client(timeout=_TIMEOUT_S, transport=transport) as client:
            resp = client.get(graph_url() + "/", params=params)
    except httpx.HTTPError as exc:
        logger.info("meta.ad_names_fetch_failed", extra={"error": str(exc)})
        return {}
    if resp.status_code != 200:
        logger.info("meta.ad_names_fetch_non_200", extra={"status": resp.status_code})
        return {}
    try:
        body = resp.json()
    except ValueError:
        return {}
    if not isinstance(body, dict):
        return {}

    out: dict[str, dict[str, str | None]] = {}
    for ad_id, node in body.items():
        if not isinstance(node, dict):
            continue
        campaign = node.get("campaign") or {}
        adset = node.get("adset") or {}
        creative = node.get("creative") or {}
        out[ad_id] = {
            "ad_name": node.get("name"),
            "campaign_name": campaign.get("name"),
            "campaign_id": campaign.get("id"),
            "adset_id": adset.get("id"),
            "adset_name": adset.get("name"),
            "thumbnail_url": creative.get("thumbnail_url"),
        }
    return out
