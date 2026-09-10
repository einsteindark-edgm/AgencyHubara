"""Enrichment de nombres reales de campañas vía Meta Marketing API.

Problema (caso real 2026-07-01): el dashboard nombra las campañas por el
`headline` del referral de WhatsApp — que es el TEXTO DEL CTA del ad
("Chatea con nosotros"), no el nombre de la campaña en Ads Manager ("Día
del Padre"). El operador no reconoce sus campañas y dos ads con el mismo
CTA se ven idénticos.

El fetch (`fetch_meta_ad_names`) vive en platform (`src.platform.meta.ad_names`,
expuesto por `src.sdk.connectorkit`) porque chats también lo usa para el origen
de cada conversación. Resuelve los nombres REALES con UN GET batch a Graph API:

    GET {graph_url()}/?ids=<ad_id,...>
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

from src.plugins.ads.aggregation import (
    SYNTHETIC_CAMPAIGN_IDS,
    AdsCampaignSummary,
)
from src.sdk.connectorkit import fetch_meta_ad_names

logger = logging.getLogger(__name__)

__all__ = ["enrich_campaign_names", "fetch_meta_ad_names"]


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
        if camp.id in SYNTHETIC_CAMPAIGN_IDS or not display:
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
