"""Reparte las conversaciones SEEDED de Día del padre entre sus segmentos.

Segmentación (2026-07-10): el dashboard ahora agrupa campaña → ad set → ads.
El dataset de prueba (20 conversaciones + 12 ventas, 2026-07-09) quedó TODO
atribuido a un solo ad → un solo segmento. Este script reparte esas sesiones
entre los segmentos REALES de la campaña para ver la sección funcional:

  Campaña "Día del padre" (120243118818600317) — segmentos reales:
    * 120243120899780317  "dia del padre segmentacion detallada"  (ACTIVE)
    * 120243120801900317  "dia del padre segmentación abierta"    (PAUSED)
    * 120243118818610317  "dia del padre segmentacion similar"    (PAUSED)

Garantías (plan puro `plan_segment_spread`, testeado):
  * SOLO sesiones `seeded_test: true` cuyo origin apunta a ads de ESTA
    campaña. Conversaciones reales y otras campañas: intactas.
  * Round-robin determinista por session_key → idempotente (re-correr
    produce el mismo reparto).
  * Sesiones con venta emiten el patch de la orden Medusa (meta_ad_id +
    meta_adset_id/meta_adset_name nuevos) — la atribución orden↔segmento
    queda coherente con el vault.

USO (dentro del container del API en la caja, igual que el backfill):

    # dry-run (default): imprime el plan, NO escribe
    cd hubara_agency && uv run python scripts/spread_dia_del_padre_segments.py

    # aplicar (vault + Medusa)
    cd hubara_agency && uv run python scripts/spread_dia_del_padre_segments.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

import httpx
from loguru import logger

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.logging import setup_logging
from src.platform.medusa.composition import get_medusa_client
from src.plugins.ads.synthetic_seed import plan_segment_spread

setup_logging()

CAMPAIGN_ID = "120243118818600317"  # Día del padre
_GRAPH = "https://graph.facebook.com/v25.0"


def _meta_token() -> str:
    try:
        from src.plugins.ads.meta.composition import get_token_store

        token = get_token_store().load()
        if token:
            return token.access_token
    except Exception:  # noqa: BLE001 — sin SSM (dev local) caemos al env
        pass
    return os.environ.get("META_SYSTEM_USER_TOKEN", "")


def _fetch_campaign_ads(token: str) -> dict[str, tuple[str, str]]:
    """`{ad_id: (adset_id, adset_name)}` de la campaña vía Graph."""
    resp = httpx.get(
        f"{_GRAPH}/{CAMPAIGN_ID}/ads",
        params={"fields": "id,adset{id,name}", "limit": "200"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15.0,
    )
    resp.raise_for_status()
    out: dict[str, tuple[str, str]] = {}
    for ad in resp.json().get("data", []):
        adset = ad.get("adset") or {}
        if ad.get("id") and adset.get("id"):
            out[ad["id"]] = (adset["id"], adset.get("name") or "")
    return out


def _pick_targets(ads: dict[str, tuple[str, str]]) -> list[str]:
    """Un ad representativo por segmento (orden estable por adset_id)."""
    by_adset: dict[str, str] = {}
    for ad_id in sorted(ads):
        adset_id = ads[ad_id][0]
        by_adset.setdefault(adset_id, ad_id)
    return [by_adset[k] for k in sorted(by_adset)]


def _load_sessions() -> list[tuple[str, dict]]:
    out = []
    for meta_file in sorted(WORKSPACE_VAULT_DIR.glob("wa_*/metadata.json")):
        try:
            out.append(
                (meta_file.parent.name, json.loads(meta_file.read_text(encoding="utf-8")))
            )
        except (OSError, json.JSONDecodeError):
            continue
    return out


async def _patch_order(client, order_id: str, patch: dict) -> None:
    from src.platform.medusa.client import MedusaAPIError

    try:
        await client.patch_draft_order_metadata(order_id, patch)
    except MedusaAPIError as exc:
        if exc.status_code != 404:
            raise
        await client.patch_order_metadata(order_id, patch)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="escribir (default: dry-run)")
    args = parser.parse_args()

    token = _meta_token()
    if not token:
        raise SystemExit("sin token Meta (SSM/META_SYSTEM_USER_TOKEN) — no puedo resolver segmentos")

    ads = _fetch_campaign_ads(token)
    targets = _pick_targets(ads)
    logger.info("ads de la campaña: {} · segmentos: {}", len(ads),
                sorted({a[0] for a in ads.values()}))
    logger.info("ads destino (1 por segmento): {}", targets)
    if len(targets) < 2:
        raise SystemExit("la campaña tiene <2 segmentos — nada que repartir")

    sessions = _load_sessions()
    plan = plan_segment_spread(sessions, frozenset(ads), targets)
    for p in plan:
        adset_id, adset_name = ads[p["new_source_id"]]
        logger.info(
            "  {} → ad {} · segmento {} ({}){}",
            p["session_key"], p["new_source_id"], adset_name, adset_id,
            f" · órdenes {p['order_ids']}" if p["order_ids"] else "",
        )
    logger.info("plan: {} sesiones seeded a repartir (de {} en vault)",
                len(plan), len(sessions))

    if not args.apply:
        logger.info("DRY-RUN — nada escrito. Re-correr con --apply para aplicar.")
        return

    # 1) vault
    for p in plan:
        meta_file = WORKSPACE_VAULT_DIR / p["session_key"] / "metadata.json"
        meta_file.write_text(
            json.dumps(p["metadata"], indent=2, ensure_ascii=False), encoding="utf-8"
        )
    logger.info("vault: {} sesiones reescritas", len(plan))

    # 2) Medusa — re-estampa la atribución de las ventas movidas
    client = get_medusa_client()
    ok = failed = 0
    for p in plan:
        adset_id, adset_name = ads[p["new_source_id"]]
        patch = {
            "meta_ad_id": p["new_source_id"],
            "meta_campaign_id": CAMPAIGN_ID,
            "meta_adset_id": adset_id,
            "meta_adset_name": adset_name,
        }
        for order_id in p["order_ids"]:
            try:
                await _patch_order(client, order_id, patch)
                ok += 1
            except Exception as exc:  # noqa: BLE001 — reportar y seguir
                failed += 1
                logger.warning("  ! {} no se pudo re-estampar: {}", order_id, type(exc).__name__)
    logger.info("Medusa: {} órdenes re-estampadas · {} fallidas", ok, failed)


if __name__ == "__main__":
    asyncio.run(main())
