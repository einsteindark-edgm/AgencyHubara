"""Plan de backfill de atribución CTWA en órdenes Medusa históricas.

Reglas (pedido 2026-07-09):
- orden con order_id que el vault conoce (sesión CTWA) → atribución REAL
  (`meta_ad_id` + `meta_campaign_id` resuelto) + marker "real".
- orden desconocida → `meta_campaign_id` SEMBRADO round-robin sobre las
  campañas reales + marker "seeded" (histórico de prueba, identificable).
- orden que ya tiene atribución → skip (idempotente, re-correr es seguro).
"""
from __future__ import annotations

import json

from src.plugins.ads.attribution_backfill import (
    build_vault_attribution_index,
    plan_order_patches,
)


def _write_session(vault, key: str, meta: dict) -> None:
    d = vault / key
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")


def test_vault_index_maps_order_ids_to_ad_attribution(tmp_path) -> None:
    _write_session(
        tmp_path,
        "wa_573001",
        {
            "origin": {"channel": "ad", "source_id": "120210000000000001"},
            "episodes": [
                {"episode_id": "ep1", "order_id": "draft_A", "closing_tag": "COMPRA_EXITOSA"},
                {"episode_id": "ep2", "order_id": None, "closing_tag": None},
            ],
            "registered_orders_history": [{"order_id": "draft_B", "success": True}],
        },
    )
    # sesión directa (sin origin) — sus órdenes NO entran al índice
    _write_session(
        tmp_path,
        "wa_573002",
        {"episodes": [{"episode_id": "e", "order_id": "draft_C"}]},
    )
    idx = build_vault_attribution_index(tmp_path)
    assert idx["draft_A"] == {"meta_ad_id": "120210000000000001", "attribution_channel": "ad"}
    assert idx["draft_B"]["meta_ad_id"] == "120210000000000001"
    assert "draft_C" not in idx


def test_plan_real_attribution_with_campaign_resolution() -> None:
    orders = [{"id": "draft_A", "metadata": {"session_key": "wa_573001"}}]
    idx = {"draft_A": {"meta_ad_id": "AD_1", "attribution_channel": "ad"}}
    plan = plan_order_patches(orders, idx, {"AD_1": "c-77"}, ["c-77", "c-88"])
    assert plan == [
        {
            "order_id": "draft_A",
            "action": "real",
            "patch": {
                "meta_ad_id": "AD_1",
                "attribution_channel": "ad",
                "meta_campaign_id": "c-77",
                "attribution_backfilled": "real",
            },
        }
    ]


def test_plan_seeds_unknown_orders_round_robin() -> None:
    orders = [
        {"id": "draft_X", "metadata": {}},
        {"id": "draft_Y", "metadata": None},
        {"id": "draft_Z", "metadata": {}},
    ]
    plan = plan_order_patches(orders, {}, {}, ["c-1", "c-2"])
    assert [p["patch"]["meta_campaign_id"] for p in plan] == ["c-1", "c-2", "c-1"]
    assert all(p["action"] == "seeded" for p in plan)
    assert all(p["patch"]["attribution_backfilled"] == "seeded" for p in plan)


def test_plan_skips_orders_already_attributed() -> None:
    orders = [
        {"id": "draft_done", "metadata": {"meta_campaign_id": "c-1"}},
        {"id": "draft_done2", "metadata": {"meta_ad_id": "AD_9"}},
        {"id": "draft_new", "metadata": {}},
    ]
    plan = plan_order_patches(orders, {}, {}, ["c-1"])
    assert [p["order_id"] for p in plan if p["action"] != "skip"] == ["draft_new"]
    assert [p["order_id"] for p in plan if p["action"] == "skip"] == [
        "draft_done",
        "draft_done2",
    ]


def test_plan_real_without_campaign_resolution_keeps_ad_id() -> None:
    # Graph no resolvió el ad (token caído / ad borrado): igual estampamos el
    # ad id real — la campaña se puede resolver después.
    orders = [{"id": "draft_A", "metadata": {}}]
    idx = {"draft_A": {"meta_ad_id": "AD_1", "attribution_channel": "ad"}}
    plan = plan_order_patches(orders, idx, {}, ["c-1"])
    patch = plan[0]["patch"]
    assert patch["meta_ad_id"] == "AD_1"
    assert "meta_campaign_id" not in patch
    assert patch["attribution_backfilled"] == "real"
