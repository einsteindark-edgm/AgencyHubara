"""Enrichment de nombres reales de campañas vía Meta Marketing API.

Problema (caso real 2026-07-01): el dashboard nombra las campañas por el
`headline` del referral de WhatsApp — que es el TEXTO DEL CTA del ad
("Chatea con nosotros"), no el nombre de la campaña en Ads Manager ("Día
del Padre"). El operador no reconoce sus campañas y dos ads con el mismo
CTA se ven idénticos.

Este módulo resuelve los nombres REALES con UN GET batch a Graph API:

    GET https://graph.facebook.com/v23.0/?ids=<ad_id,...>
        &fields=name,campaign{id,name}&access_token=...

Los `source_id` del referral de CTWA son ad ids — Graph los resuelve
directo. Requiere scope `ads_read` (el System User token del tenant ya lo
tiene — ver infra/whatsapp-provisioning/README.md §0).

Diseño:
  * `fetch_meta_ad_names` — el ÚNICO I/O. Best-effort: sin token, sin ids,
    HTTP != 200 o error de red → `{}` (el dashboard degrada a headlines,
    nunca bloquea ni levanta).
  * `enrich_campaign_names` — pura: reescribe los summaries con
    `dataclasses.replace`. El headline original NO se pierde (pasa a
    `creative_title`).
  * El cache TTL vive en la capa API (igual que el scan del vault) — acá
    no hay estado (R-STATELESS-friendly aunque no es activity).
"""
from __future__ import annotations

import dataclasses
import logging

import httpx

from src.plugins.ads.aggregation import DIRECT_CAMPAIGN_ID, AdsCampaignSummary

logger = logging.getLogger(__name__)

_GRAPH_URL = "https://graph.facebook.com/v23.0/"
_FIELDS = "name,campaign{id,name},adset{id,name},creative{thumbnail_url}"
_TIMEOUT_S = 4.0


def fetch_meta_ad_names(
    ad_ids: list[str],
    *,
    token: str,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, dict[str, str | None]]:
    """Resuelve `{ad_id: {ad_name, campaign_name, campaign_id}}` en un call.

    Best-effort: cualquier problema (sin token, ids vacíos, HTTP error,
    red caída, payload inesperado) devuelve `{}` — el caller sigue con los
    headlines del referral. Timeout corto (4s) para no colgar el endpoint
    del dashboard si Graph está lento.
    """
    if not token or not ad_ids:
        return {}
    params = {
        "ids": ",".join(ad_ids),
        "fields": _FIELDS,
        "access_token": token,
    }
    try:
        with httpx.Client(timeout=_TIMEOUT_S, transport=transport) as client:
            resp = client.get(_GRAPH_URL, params=params)
    except httpx.HTTPError as exc:
        logger.info("ads.meta_names_fetch_failed", extra={"error": str(exc)})
        return {}
    if resp.status_code != 200:
        logger.info(
            "ads.meta_names_fetch_non_200",
            extra={"status": resp.status_code},
        )
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


def _display_name(info: dict[str, str | None]) -> str | None:
    """`"Campaña · Ad"` si hay ambos; el que exista si hay uno; None si nada."""
    campaign_name = info.get("campaign_name")
    ad_name = info.get("ad_name")
    if campaign_name and ad_name:
        return f"{campaign_name} · {ad_name}"
    return campaign_name or ad_name


def enrich_campaign_names(
    campaigns: list[AdsCampaignSummary],
    names: dict[str, dict[str, str | None]],
) -> list[AdsCampaignSummary]:
    """Reescribe `name` con el nombre real de Meta (pura, sin I/O).

    Reglas:
      * El bucket sintético `direct` nunca se toca.
      * id sin entry en `names` (o sin nombre resoluble) → summary intacto
        (misma instancia — barato y test-friendly).
      * El headline original pasa a `creative_title` (no se pierde) y
        `meta_campaign_id` se llena si vino.
    """
    out: list[AdsCampaignSummary] = []
    for camp in campaigns:
        info = names.get(camp.id)
        display = _display_name(info) if info else None
        if camp.id == DIRECT_CAMPAIGN_ID or not display:
            out.append(camp)
            continue
        out.append(
            dataclasses.replace(
                camp,
                name=display,
                creative_title=camp.name,
                creative_thumbnail_url=info.get("thumbnail_url") if info else None,
                meta_campaign_id=info.get("campaign_id") if info else None,
                meta_adset_id=info.get("adset_id") if info else None,
                ad_set=info.get("adset_name") if info else None,
            )
        )
    return out


__all__ = ["fetch_meta_ad_names", "enrich_campaign_names"]
