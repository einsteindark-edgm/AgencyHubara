"""Backfill de atribución CTWA en órdenes Medusa históricas (2026-07-09).

Las órdenes registradas ANTES del forward-stamping no llevan `meta_ad_id` en
su metadata. Este módulo arma el plan de parcheo (PURO — el IO vive en
`scripts/backfill_order_ad_attribution.py`):

- Órdenes cuya venta el vault CONOCE (episode/registro con ese order_id en una
  sesión con `origin.source_id`) → atribución REAL (`meta_ad_id` + campaña
  resuelta vía Graph) con `attribution_backfilled="real"`.
- El resto → SOLO con seeds explícitos (`--campaign-ids`): `meta_campaign_id`
  round-robin con `attribution_backfilled="seeded"` — histórico de PRUEBA
  identificable/limpiable por el marker. Sin seeds quedan intactas (unmatched):
  sembrar por default contaminaría campañas (2026-07-09: seeds solo a Día del
  padre; Duo zodiacal queda limpia).
- Órdenes que ya tienen atribución → skip (idempotente).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_vault_attribution_index(vault_dir: Path) -> dict[str, dict[str, Any]]:
    """`{order_id: {meta_ad_id, attribution_channel}}` de las sesiones CTWA.

    Recorre `<vault>/*/metadata.json`; solo sesiones con `origin.source_id`
    aportan (el source_id del referral ES el ad id de Meta). Los order_ids
    salen de `episodes[].order_id` + `registered_orders_history[].order_id`.
    Best-effort: metadata rota se saltea.
    """
    index: dict[str, dict[str, Any]] = {}
    for meta_file in sorted(vault_dir.glob("*/metadata.json")):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        origin = meta.get("origin") or {}
        source_id = origin.get("source_id")
        if not source_id:
            continue
        attribution = {
            "meta_ad_id": str(source_id),
            "attribution_channel": origin.get("channel"),
        }
        order_ids = [e.get("order_id") for e in meta.get("episodes") or []]
        order_ids += [
            r.get("order_id") for r in meta.get("registered_orders_history") or []
        ]
        for oid in order_ids:
            if oid:
                index[str(oid)] = attribution
    return index


def plan_order_patches(
    orders: list[dict[str, Any]],
    vault_index: dict[str, dict[str, Any]],
    ad_to_campaign: dict[str, str],
    seed_campaign_ids: list[str],
) -> list[dict[str, Any]]:
    """Plan de parcheo por orden: `{order_id, action: real|seeded|skip|unmatched, patch}`.

    Idempotente: órdenes que ya tienen `meta_ad_id`/`meta_campaign_id` → skip.
    Sembrar es EXPLÍCITO: sin `seed_campaign_ids` las órdenes sin rastro CTWA
    quedan intactas (`unmatched`) — un default que siembra contamina campañas
    (caso 2026-07-09: solo Día del padre recibe seeds; Duo zodiacal queda limpia).
    El round-robin de seeds es determinista en el orden de entrada.
    """
    plan: list[dict[str, Any]] = []
    seed_i = 0
    for order in orders:
        oid = str(order.get("id"))
        metadata = order.get("metadata") or {}
        if metadata.get("meta_ad_id") or metadata.get("meta_campaign_id"):
            plan.append({"order_id": oid, "action": "skip", "patch": {}})
            continue
        real = vault_index.get(oid)
        if real:
            patch = dict(real)
            campaign = ad_to_campaign.get(real["meta_ad_id"])
            if campaign:
                patch["meta_campaign_id"] = campaign
            patch["attribution_backfilled"] = "real"
            plan.append({"order_id": oid, "action": "real", "patch": patch})
            continue
        if not seed_campaign_ids:
            plan.append({"order_id": oid, "action": "unmatched", "patch": {}})
            continue
        campaign = seed_campaign_ids[seed_i % len(seed_campaign_ids)]
        seed_i += 1
        plan.append(
            {
                "order_id": oid,
                "action": "seeded",
                "patch": {
                    "meta_campaign_id": campaign,
                    "attribution_backfilled": "seeded",
                },
            }
        )
    return plan
