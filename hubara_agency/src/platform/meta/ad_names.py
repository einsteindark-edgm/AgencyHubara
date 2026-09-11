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
# Graph acepta máximo 50 ids por `?ids=` — más, y responde error para TODO el
# lote. Se trocea (2026-09-10): antes, pasar de 50 anuncios con chats tiraba la
# jerarquía entera a headlines.
_IDS_PER_CALL = 50


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
    out: dict[str, dict[str, str | None]] = {}
    with httpx.Client(timeout=_TIMEOUT_S, transport=transport) as client:
        for start in range(0, len(ad_ids), _IDS_PER_CALL):
            batch = ad_ids[start : start + _IDS_PER_CALL]
            # Un lote fallido no borra los demás: resultado parcial > nada.
            out.update(_resolve_batch(client, batch, token))
    return out


def _resolve_batch(
    client: httpx.Client, ad_ids: list[str], token: str
) -> dict[str, dict[str, str | None]]:
    """Resuelve un lote; si Graph lo rechaza (un id inválido — anuncio borrado —
    invalida el `?ids=` ENTERO, visto en vivo 2026-09-10) lo parte en mitades
    hasta aislar el id malo: log2(50) ≈ 6 calls extra, no uno por id. Un error
    de red NO bisecta (multiplicaría timeouts): ese lote queda vacío."""
    result = _fetch_batch(client, ad_ids, token)
    if result is not None:
        return result
    if len(ad_ids) == 1:
        logger.info("meta.ad_names_bad_id", extra={"ad_id": ad_ids[0]})
        return {}
    mid = len(ad_ids) // 2
    return {
        **_resolve_batch(client, ad_ids[:mid], token),
        **_resolve_batch(client, ad_ids[mid:], token),
    }


def _fetch_batch(
    client: httpx.Client, ad_ids: list[str], token: str
) -> dict[str, dict[str, str | None]] | None:
    """Un GET batch. `None` = Graph rechazó el lote (candidato a bisección);
    `{}` = error de red / respuesta ilegible (no se reintenta)."""
    params = {
        "ids": ",".join(ad_ids),
        "fields": _FIELDS,
        "access_token": token,
    }
    try:
        resp = client.get(graph_url() + "/", params=params)
    except httpx.HTTPError as exc:
        logger.info("meta.ad_names_fetch_failed", extra={"error": str(exc)})
        return {}
    if resp.status_code != 200:
        logger.info("meta.ad_names_fetch_non_200", extra={"status": resp.status_code})
        return None
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
