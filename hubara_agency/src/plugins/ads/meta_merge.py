"""Merge PURO de campañas Meta (Marketing API) sobre las campañas del vault.

Las campañas del dashboard salen del vault (buckets por `source_id` del
referral CTWA); las de Meta salen del Marketing API. Este módulo las une en
UNA lista para que la UI pinte todas igual (lista izquierda + canvas central):

- bucket del vault cuyo `meta_campaign_id` matchea UNA campaña Meta → se le
  llenan los campos Meta (spend/impressions/reach/clicks/status/objective).
- campaña Meta sin bucket (o con VARIOS buckets: llenar campaign-level spend
  en N buckets duplicaría el gasto) → entra como entrada standalone con los
  campos del vault vacíos (started=0, conversations=None).

Puro (sin IO): el fetch vive en la capa API con cache TTL.
"""
from __future__ import annotations

from src.plugins.ads.aggregation import AdsCampaignSummary
from src.plugins.ads.meta.client import MetaCampaignMeta
from src.plugins.ads.meta.parse import MetaCampaignMetrics


def merge_meta_campaigns(
    campaigns: list[AdsCampaignSummary],
    meta_campaigns: list[MetaCampaignMeta],
    metrics: list[MetaCampaignMetrics],
) -> list[AdsCampaignSummary]:
    import dataclasses

    metrics_by_id = {m.campaign_id: m for m in metrics}
    meta_by_id = {c.campaign_id: c for c in meta_campaigns}
    # Insights y list_campaigns pueden diferir (campaña archivada aparece en uno
    # solo) — la unión, con orden estable, cubre ambos.
    all_ids = list(dict.fromkeys([*metrics_by_id, *meta_by_id]))

    buckets_by_campaign: dict[str, list[int]] = {}
    for i, b in enumerate(campaigns):
        if b.meta_campaign_id:
            buckets_by_campaign.setdefault(b.meta_campaign_id, []).append(i)

    out = list(campaigns)
    for cid in all_ids:
        m = metrics_by_id.get(cid)
        meta = meta_by_id.get(cid)
        fields = {
            "spend": m.spend if m else None,
            "impressions": m.impressions if m else None,
            "reach": m.reach if m else None,
            "clicks": m.clicks if m else None,
            # el contract de la UI usa status en minúscula ("active"/"paused")
            "status": meta.status.lower() if meta and meta.status else None,
            "objective": meta.objective if meta else None,
            "meta_campaign_id": cid,
        }
        idxs = buckets_by_campaign.get(cid, [])
        if len(idxs) == 1:
            out[idxs[0]] = dataclasses.replace(out[idxs[0]], **fields)
        else:
            name = (meta.name if meta else None) or (m.campaign_name if m else None) or cid
            out.append(
                AdsCampaignSummary(
                    id=cid,
                    name=name,
                    source_type="ad",
                    started=0,
                    first_seen_ms=None,
                    last_seen_ms=None,
                    conversations=None,
                    **fields,
                )
            )
    return out
