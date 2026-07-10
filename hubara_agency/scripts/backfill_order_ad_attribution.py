"""Backfill de atribución CTWA (`meta_ad_id`/`meta_campaign_id`) en órdenes Medusa.

Contexto (2026-07-09): las órdenes registradas ANTES del forward-stamping no
llevan atribución en metadata. Este script converge el histórico:

  * Orden cuya venta el VAULT conoce (episodio/registro con ese order_id en una
    sesión CTWA) → atribución REAL: `meta_ad_id` del referral + campaña resuelta
    vía Graph (`attribution_backfilled="real"`).
  * Orden sin rastro CTWA → SOLO si pasás `--campaign-ids`: `meta_campaign_id`
    sembrado round-robin sobre ESAS campañas (`attribution_backfilled="seeded"`)
    — histórico de PRUEBA identificable/reversible por el marker. Sin el flag,
    quedan INTACTAS (sembrar es explícito; no contaminamos campañas por default
    — caso 2026-07-09: seeds solo a Día del padre, Duo zodiacal limpia).
  * Orden que ya tiene atribución → skip. IDEMPOTENTE: re-correr es seguro.

USO (dentro del container del API en la caja, que tiene vault + SSM + Medusa):

    # dry-run (default): imprime el plan, NO escribe
    cd hubara_agency && uv run python scripts/backfill_order_ad_attribution.py

    # aplicar de verdad
    cd hubara_agency && uv run python scripts/backfill_order_ad_attribution.py --apply

    # sembrar las órdenes sin rastro CTWA en UNA campaña (Día del padre 2026)
    ... --apply --campaign-ids 120243118818600317

No imprime tokens. Requiere Medusa configurado + la conexión Meta sembrada
(token store /hubara/<tenant>/meta/oauth) o META_SYSTEM_USER_TOKEN en env.
"""
from __future__ import annotations

import argparse
import asyncio
import os

from loguru import logger

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.logging import setup_logging
from src.platform.medusa.composition import get_medusa_client
from src.plugins.ads.attribution_backfill import (
    build_vault_attribution_index,
    plan_order_patches,
)
from src.plugins.ads.meta_names import fetch_meta_ad_names

setup_logging()

_PAGE = 100


async def _patch_metadata(client, order_id: str, patch: dict) -> None:
    """Draft primero; si la orden ya fue CONVERTIDA (draft→order, 404 en
    /admin/draft-orders) cae a `patch_order_metadata` (/admin/orders) — caso
    real de la primera corrida: las 13 históricas eran órdenes consumadas."""
    from src.platform.medusa.client import MedusaAPIError

    try:
        await client.patch_draft_order_metadata(order_id, patch)
    except MedusaAPIError as exc:
        if exc.status_code != 404:
            raise
        await client.patch_order_metadata(order_id, patch)


async def _list_all_orders(client) -> list[dict]:
    orders: list[dict] = []
    offset = 0
    while True:
        page = await client.list_orders(
            limit=_PAGE, offset=offset, fields="id,display_id,status,metadata,created_at"
        )
        batch = page.get("orders") or []
        orders.extend(batch)
        if len(batch) < _PAGE:
            return orders
        offset += _PAGE


def _meta_token() -> str:
    """Token para Graph: la conexión sembrada (SSM) primero, env como fallback."""
    try:
        from src.plugins.ads.meta.composition import get_token_store

        token = get_token_store().load()
        if token:
            return token.access_token
    except Exception:  # noqa: BLE001 — sin SSM (dev local) caemos al env
        pass
    return os.environ.get("META_SYSTEM_USER_TOKEN", "")


def _seed_campaigns(campaign_ids_arg: str) -> list[str]:
    """Campañas seed SOLO explícitas (--campaign-ids). Vacío = no sembrar:
    las órdenes sin rastro CTWA quedan intactas (solo atribución real)."""
    return [c.strip() for c in campaign_ids_arg.split(",") if c.strip()]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="escribir (default: dry-run)")
    parser.add_argument("--campaign-ids", default="", help="ids seed coma-separados")
    args = parser.parse_args()

    client = get_medusa_client()
    orders = await _list_all_orders(client)
    logger.info("órdenes en Medusa: {}", len(orders))

    vault_index = build_vault_attribution_index(WORKSPACE_VAULT_DIR)
    logger.info("órdenes con atribución REAL en el vault: {}", len(vault_index))

    token = _meta_token()
    # Ads a resolver: los del vault + los ya estampados en órdenes (para el
    # upgrade de segmento sobre órdenes atribuidas antes de la segmentación).
    real_ad_ids = sorted(
        {v["meta_ad_id"] for v in vault_index.values()}
        | {
            str((o.get("metadata") or {}).get("meta_ad_id"))
            for o in orders
            if (o.get("metadata") or {}).get("meta_ad_id")
        }
    )
    names = fetch_meta_ad_names(real_ad_ids, token=token) if real_ad_ids and token else {}
    if real_ad_ids and token and not names:
        # El batch `?ids=` de Graph devuelve 400 si UN solo id es inválido
        # (ad borrado / metadata operator-writable con basura) → names={} y el
        # plan degrada silenciosamente (0 upgrades, patches sin campaña/adset).
        # Hacerlo VISIBLE: el operador decide si depura ids o corre igual.
        logger.warning(
            "Graph no resolvió NINGÚN ad ({} ids) — probable id inválido en el "
            "batch. El plan seguirá sin campañas/segmentos resueltos.",
            len(real_ad_ids),
        )
    ad_to_campaign = {
        ad: info["campaign_id"] for ad, info in names.items() if info.get("campaign_id")
    }
    # Segmentación (2026-07-10): ad → (adset_id, adset_name) para estampar el
    # segmento en las órdenes (nuevas y upgrade de las ya atribuidas).
    ad_to_adset = {
        ad: (info["adset_id"], info.get("adset_name") or "")
        for ad, info in names.items()
        if info.get("adset_id")
    }

    seeds = _seed_campaigns(args.campaign_ids)
    logger.info(
        "campañas seed (round-robin): {}",
        seeds or "(ninguna — solo atribución real; --campaign-ids para sembrar)",
    )

    plan = plan_order_patches(
        orders, vault_index, ad_to_campaign, seeds, ad_to_adset=ad_to_adset
    )
    by_action = {"real": 0, "seeded": 0, "adset_upgrade": 0, "skip": 0, "unmatched": 0}
    for p in plan:
        by_action[p["action"]] += 1
        if p["action"] in ("real", "seeded", "adset_upgrade"):
            logger.info("  {} {} → {}", p["action"].upper(), p["order_id"], p["patch"])
    logger.info(
        "plan: {} real · {} seeded · {} adset_upgrade · {} skip · {} unmatched (intactas)",
        by_action["real"], by_action["seeded"], by_action["adset_upgrade"],
        by_action["skip"], by_action["unmatched"],
    )

    if not args.apply:
        logger.info("DRY-RUN — nada escrito. Re-correr con --apply para aplicar.")
        return

    ok = failed = 0
    for p in plan:
        if p["action"] not in ("real", "seeded", "adset_upgrade"):
            continue
        try:
            await _patch_metadata(client, p["order_id"], p["patch"])
            ok += 1
        except Exception as exc:  # noqa: BLE001 — reportar y seguir con el resto
            failed += 1
            logger.warning("  ! {} no se pudo parchear: {}", p["order_id"], type(exc).__name__)
    logger.info("APLICADO: {} ok · {} fallidas", ok, failed)


if __name__ == "__main__":
    asyncio.run(main())
