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
import datetime
import logging

import httpx

from src.plugins.ads.aggregation import DIRECT_CAMPAIGN_ID, AdsCampaignSummary

logger = logging.getLogger(__name__)

_GRAPH_URL = "https://graph.facebook.com/v23.0/"
# name/status/adset del ad + campaña (nombre real, objetivo, start) +
# insights LIFETIME (date_preset=maximum) — todo en UN field expansion,
# un solo GET batch para N ads. Ver META_ADS_INTEGRATION.md §7.1.
_FIELDS = (
    "name,effective_status,adset{name},"
    "campaign{id,name,objective,start_time},"
    "insights.date_preset(maximum){spend,impressions,reach,clicks}"
)
_TIMEOUT_S = 4.0


# effective_status de Meta → status del dashboard ("active" | "paused" |
# lowercase de lo que sea). *_PAUSED cubre CAMPAIGN_PAUSED / ADSET_PAUSED.
def _map_status(effective_status: str | None) -> str | None:
    if not effective_status:
        return None
    if effective_status == "ACTIVE":
        return "active"
    if effective_status.endswith("PAUSED"):
        return "paused"
    return effective_status.lower()


def _map_objective(objective: str | None) -> str | None:
    """`OUTCOME_SALES` -> `Sales` (legible, matchea el vocabulario simple
    de la UI sin inventar traducciones)."""
    if not objective:
        return None
    return objective.removeprefix("OUTCOME_").replace("_", " ").title()


def _to_float(raw: object) -> float | None:
    """Graph devuelve los numeros de insights como strings."""
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _to_int(raw: object) -> int | None:
    f = _to_float(raw)
    return int(f) if f is not None else None


def fetch_meta_ad_names(
    ad_ids: list[str],
    *,
    token: str,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, dict[str, object]]:
    """Resuelve `{ad_id: {nombres + status + adset + insights lifetime}}`
    en UN call batch.

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

    out: dict[str, dict[str, object]] = {}
    for ad_id, node in body.items():
        if not isinstance(node, dict):
            continue
        campaign = node.get("campaign") or {}
        adset = node.get("adset") or {}
        insights_rows = (node.get("insights") or {}).get("data") or []
        insights = (
            insights_rows[0]
            if isinstance(insights_rows, list) and insights_rows
            else {}
        )
        if not isinstance(insights, dict):
            insights = {}
        out[ad_id] = {
            "ad_name": node.get("name"),
            "campaign_name": campaign.get("name"),
            "campaign_id": campaign.get("id"),
            "status": _map_status(node.get("effective_status")),
            "objective": _map_objective(campaign.get("objective")),
            "ad_set": adset.get("name"),
            "campaign_start_time": campaign.get("start_time"),
            "spend": _to_float(insights.get("spend")),
            "impressions": _to_int(insights.get("impressions")),
            "reach": _to_int(insights.get("reach")),
            "clicks": _to_int(insights.get("clicks")),
        }
    return out


def _days_run(start_time: object, now_ms: int) -> int | None:
    """Dias corridos desde el `start_time` ISO de la campana Meta."""
    if not isinstance(start_time, str) or not start_time:
        return None
    try:
        start = datetime.datetime.fromisoformat(start_time)
    except ValueError:
        return None
    now = datetime.datetime.fromtimestamp(
        now_ms / 1000, tz=datetime.timezone.utc
    )
    return max((now - start).days, 0)


def _display_name(info: dict[str, object]) -> str | None:
    """`"Campaña · Ad"` si hay ambos; el que exista si hay uno; None si nada."""
    campaign_name = info.get("campaign_name")
    ad_name = info.get("ad_name")
    if campaign_name and ad_name:
        return f"{campaign_name} · {ad_name}"
    return campaign_name or ad_name  # type: ignore[return-value]


def enrich_campaign_names(
    campaigns: list[AdsCampaignSummary],
    names: dict[str, dict[str, object]],
    *,
    now_ms: int | None = None,
) -> list[AdsCampaignSummary]:
    """Reescribe nombre + metricas Meta en los summaries (pura, sin I/O).

    Reglas:
      * El bucket sintetico `direct` nunca se toca.
      * id sin entry en `names` (o sin nombre resoluble) -> summary intacto
        (misma instancia — barato y test-friendly).
      * El headline original pasa a `creative_title` (no se pierde) y
        `meta_campaign_id` se llena si vino.
      * Metricas (status/objective/ad_set/spend/impressions/reach/clicks/
        days_run): solo se escriben si Meta las trajo — un None de Meta
        nunca pisa un valor del vault.
      * `now_ms` inyectable para tests (default: reloj real) — se usa solo
        para derivar `days_run` desde el `start_time` de la campana.
    """
    if now_ms is None:
        import time

        now_ms = int(time.time() * 1000)

    def _keep(new: object, current: object) -> object:
        return new if new is not None else current

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
                meta_campaign_id=_keep(
                    info.get("campaign_id"), camp.meta_campaign_id
                ),
                status=_keep(info.get("status"), camp.status),
                objective=_keep(info.get("objective"), camp.objective),
                ad_set=_keep(info.get("ad_set"), camp.ad_set),
                spend=_keep(info.get("spend"), camp.spend),
                impressions=_keep(info.get("impressions"), camp.impressions),
                reach=_keep(info.get("reach"), camp.reach),
                clicks=_keep(info.get("clicks"), camp.clicks),
                days_run=_keep(
                    _days_run(info.get("campaign_start_time"), now_ms),
                    camp.days_run,
                ),
            )
        )
    return out


__all__ = ["fetch_meta_ad_names", "enrich_campaign_names"]
