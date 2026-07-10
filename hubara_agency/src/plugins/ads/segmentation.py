"""Agrupación jerárquica campaña → adset → ad buckets (PURO, sin IO).

El vault agrupa episodios por `source_id` (= AD id de Meta — el referral de
WhatsApp no trae campaña ni adset). Este módulo recompone la jerarquía real
de Ads Manager sobre esos buckets usando el resolver `fetch_meta_ad_names`
(`ad_id → {campaign_id, campaign_name, adset_id, adset_name}`):

- `group_buckets_by_campaign` — filas del listado principal (una por campaña).
- (adset grouping + scope helpers se suman en incrementos siguientes)

El IO (Graph batch, caches TTL) vive en la capa API, igual que el resto del
plugin. Best-effort en todos los niveles: bucket sin resolver pasa intacto.
"""
from __future__ import annotations

import dataclasses

from src.plugins.ads.aggregation import AdsCampaignSummary
from src.plugins.ads.classification import VALID_STATES
from src.plugins.ads.meta_names import enrich_campaign_names

Names = dict[str, dict[str, str | None]]

# Bucket sintético del drill-down para ads resueltos SIN adset (nodo raro de
# Graph / post orgánico): visibles como "Sin segmento", nunca desaparecen.
UNSEGMENTED_ADSET_ID = "sin-segmento"
UNSEGMENTED_ADSET_NAME = "Sin segmento"


def _first(values: list, default=None):
    """Primer valor no-None de la lista (para name/source_type del grupo)."""
    for v in values:
        if v is not None:
            return v
    return default


def _merge_counts(members: list[AdsCampaignSummary]) -> dict[str, int] | None:
    dicts = [m.conversations for m in members if m.conversations is not None]
    if not dicts:
        return None
    out = {state: 0 for state in VALID_STATES}
    for d in dicts:
        for state, n in d.items():
            out[state] = out.get(state, 0) + n
    return out


def merge_bucket_group(members: list[AdsCampaignSummary]) -> AdsCampaignSummary:
    """Merge EXACTO de N buckets en una fila agregada.

    Los promedios se recomponen desde las cardinalidades (`revenue_count`,
    `duration_count`) — promediar promedios miente con buckets de tamaño
    distinto. `None` honesto se preserva: si ningún miembro tuvo data, el
    campo agregado queda None (no 0).
    """
    revenue_vals = [m.revenue for m in members if m.revenue is not None]
    revenue = sum(revenue_vals) if revenue_vals else None
    revenue_count = sum(m.revenue_count for m in members)
    avg_ticket = (
        round(revenue / revenue_count)
        if revenue is not None and revenue_count > 0
        else None
    )

    dur_count = sum(m.duration_count for m in members)
    dur_total = sum(
        (m.avg_episode_duration_ms or 0) * m.duration_count for m in members
    )
    avg_duration = round(dur_total / dur_count) if dur_count > 0 else None

    llm_vals = [m.llm_cost_usd for m in members if m.llm_cost_usd is not None]
    llm_cost = round(sum(llm_vals), 6) if llm_vals else None
    llm_tokens = (
        sum(m.llm_tokens or 0 for m in members) if llm_vals else None
    )

    firsts = [m.first_seen_ms for m in members if m.first_seen_ms is not None]
    lasts = [m.last_seen_ms for m in members if m.last_seen_ms is not None]

    return AdsCampaignSummary(
        id=members[0].id,
        name=_first([m.name for m in members]),
        source_type=_first([m.source_type for m in members]),
        started=sum(m.started for m in members),
        first_seen_ms=min(firsts) if firsts else None,
        last_seen_ms=max(lasts) if lasts else None,
        conversations=_merge_counts(members),
        revenue=revenue,
        avg_ticket=avg_ticket,
        llm_cost_usd=llm_cost,
        llm_tokens=llm_tokens,
        avg_episode_duration_ms=avg_duration,
        revenue_count=revenue_count,
        duration_count=dur_count,
        capi_leads_sent=sum(m.capi_leads_sent for m in members),
        capi_purchases_sent=sum(m.capi_purchases_sent for m in members),
        capi_failed=sum(m.capi_failed for m in members),
    )


def group_buckets_by_campaign(
    buckets: list[AdsCampaignSummary], names: Names
) -> list[AdsCampaignSummary]:
    """Agrupa los buckets (por ad) en filas por campaña Meta.

    - Bucket resuelto (names[id].campaign_id) → agrupa bajo esa campaña.
      Un solo ad → conserva el detalle por-ad (nombre "Campaña · Ad",
      creative, adset) con `id = campaign_id`. Varios ads → merge exacto
      con nombre de campaña (el detalle por-ad no aplica a la fila grupo).
    - Bucket `direct` o sin resolver → pasa INTACTO (misma instancia) —
      degradación idéntica al comportamiento pre-segmentación.

    Ordena por `last_seen_ms` descendente (criterio del listado).
    """
    grouped: dict[str, list[AdsCampaignSummary]] = {}
    passthrough: list[AdsCampaignSummary] = []
    for b in buckets:
        info = names.get(b.id)
        campaign_id = info.get("campaign_id") if info else None
        if campaign_id:
            grouped.setdefault(campaign_id, []).append(b)
        else:
            passthrough.append(b)

    out: list[AdsCampaignSummary] = []
    for campaign_id, members in grouped.items():
        if len(members) == 1:
            enriched = enrich_campaign_names(members, names)[0]
            out.append(dataclasses.replace(enriched, id=campaign_id))
            continue
        merged = merge_bucket_group(members)
        campaign_name = _first(
            [names[m.id].get("campaign_name") for m in members if m.id in names]
        )
        out.append(
            dataclasses.replace(
                merged,
                id=campaign_id,
                name=campaign_name or merged.name,
                meta_campaign_id=campaign_id,
            )
        )

    out.extend(passthrough)
    out.sort(
        key=lambda c: c.last_seen_ms if c.last_seen_ms is not None else -1,
        reverse=True,
    )
    return out


def group_buckets_by_adset(
    buckets: list[AdsCampaignSummary], names: Names
) -> list[AdsCampaignSummary]:
    """Drill-down de UNA campaña: sus buckets (ads) agrupados por ad set.

    El caller ya filtró `buckets` a los ads de la campaña. Cada fila sale con
    `id = adset_id` y `name = adset_name` (reusa el shape de campaña — el
    frontend las parsea con el mismo contrato). Ads resueltos sin adset caen
    al bucket sintético `UNSEGMENTED_ADSET_ID`.
    """
    grouped: dict[str, list[AdsCampaignSummary]] = {}
    for b in buckets:
        info = names.get(b.id) or {}
        adset_id = info.get("adset_id") or UNSEGMENTED_ADSET_ID
        grouped.setdefault(adset_id, []).append(b)

    out: list[AdsCampaignSummary] = []
    for adset_id, members in grouped.items():
        merged = merge_bucket_group(members) if len(members) > 1 else members[0]
        first_info = names.get(members[0].id) or {}
        adset_name = (
            UNSEGMENTED_ADSET_NAME
            if adset_id == UNSEGMENTED_ADSET_ID
            else _first(
                [(names.get(m.id) or {}).get("adset_name") for m in members]
            )
        )
        out.append(
            dataclasses.replace(
                merged,
                id=adset_id,
                name=adset_name or adset_id,
                ad_set=adset_name,
                meta_adset_id=(
                    None if adset_id == UNSEGMENTED_ADSET_ID else adset_id
                ),
                meta_campaign_id=first_info.get("campaign_id"),
            )
        )

    out.sort(
        key=lambda c: c.last_seen_ms if c.last_seen_ms is not None else -1,
        reverse=True,
    )
    return out


def merge_meta_adsets(
    rows: list[AdsCampaignSummary],
    metrics: list,
) -> list[AdsCampaignSummary]:
    """Métricas Meta (insights level=adset) sobre las filas de segmento.

    Espejo de `merge_meta_campaigns` a nivel adset: fila del vault matcheada
    por `adset_id` → se le llenan spend/impressions/reach/clicks/conv;
    segmento con gasto pero sin chats atribuidos → entra standalone
    (started=0) para que el operador VEA dónde gasta sin resultados; sin
    gasto y sin chats → ruido, no entra.
    """
    metrics_by_id = {m.adset_id: m for m in metrics}
    rows_by_id = {r.id: i for i, r in enumerate(rows)}

    out = list(rows)
    for adset_id, m in metrics_by_id.items():
        fields = {
            "spend": m.spend,
            "impressions": m.impressions,
            "reach": m.reach,
            "clicks": m.clicks,
            "messaging_conversations_started": m.messaging_conversations_started,
        }
        idx = rows_by_id.get(adset_id)
        if idx is not None:
            out[idx] = dataclasses.replace(out[idx], **fields)
        elif m.spend:
            out.append(
                AdsCampaignSummary(
                    id=adset_id,
                    name=m.adset_name or adset_id,
                    source_type="ad",
                    started=0,
                    first_seen_ms=None,
                    last_seen_ms=None,
                    conversations=None,
                    ad_set=m.adset_name or None,
                    meta_adset_id=adset_id,
                    meta_campaign_id=m.campaign_id,
                    **fields,
                )
            )
    return out


def collect_source_ids(sessions: list) -> frozenset[str]:
    """Ad ids (source_ids de canales Meta) presentes en el vault escaneado.

    Sale del `origin` sticky Y de los `referral_snapshot` por episodio
    (re-atribución FU2). Alimenta el resolver de nombres y el scope de los
    endpoints de drill-down sin re-agregar campañas (mucho más barato que
    `list_ads_campaigns` — no clasifica ni cuenta mensajes).
    """
    from src.plugins.ads.aggregation import _META_CHANNELS

    out: set[str] = set()
    for _session_dir, metadata in sessions:
        origin = metadata.get("origin") or {}
        if origin.get("channel") in _META_CHANNELS and origin.get("source_id"):
            out.add(origin["source_id"])
        for ep in metadata.get("episodes") or []:
            if not isinstance(ep, dict):
                continue
            snap = ep.get("referral_snapshot") or {}
            if snap.get("channel") in _META_CHANNELS and snap.get("source_id"):
                out.add(snap["source_id"])
    return frozenset(out)


def scope_source_ids(
    names: Names,
    *,
    campaign_id: str,
    adset_id: str | None = None,
) -> frozenset[str]:
    """Ad ids (source_ids del vault) que caen dentro del scope pedido.

    `campaign_id` (fila del listado agrupado) → todos sus ads; con `adset_id`
    se acota al segmento. Vacío = el id no matchea ninguna campaña resuelta —
    la capa API lo interpreta como "usar el id crudo" (back-compat con ids
    legacy = source_id y con el bucket `direct`).
    """
    out = set()
    for ad_id, info in names.items():
        if info.get("campaign_id") != campaign_id:
            continue
        if adset_id is not None and (
            info.get("adset_id") or UNSEGMENTED_ADSET_ID
        ) != adset_id:
            continue
        out.add(ad_id)
    return frozenset(out)
