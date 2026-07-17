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


def test_plan_without_seeds_leaves_unknown_orders_untouched() -> None:
    # Sin campañas seed (el operador no pasó --campaign-ids): las órdenes sin
    # rastro CTWA quedan INTACTAS (unmatched, patch vacío) — sembrar es una
    # decisión explícita, no un default que contamina campañas.
    orders = [{"id": "draft_X", "metadata": {}}]
    plan = plan_order_patches(orders, {}, {}, [])
    assert plan == [{"order_id": "draft_X", "action": "unmatched", "patch": {}}]


def test_plan_real_includes_adset_when_resolvable() -> None:
    """Segmentación (2026-07-10): el patch real estampa también el segmento
    (`meta_adset_id`/`meta_adset_name`) cuando el resolver lo trae — así las
    ventas Medusa quedan relacionadas por ad set, no solo por campaña."""
    orders = [{"id": "draft_A", "metadata": {}}]
    idx = {"draft_A": {"meta_ad_id": "AD_1", "attribution_channel": "ad"}}
    plan = plan_order_patches(
        orders,
        idx,
        {"AD_1": "c-77"},
        [],
        ad_to_adset={"AD_1": ("ADSET_A", "Hombres 25-45")},
    )
    patch = plan[0]["patch"]
    assert patch["meta_adset_id"] == "ADSET_A"
    assert patch["meta_adset_name"] == "Hombres 25-45"
    assert patch["meta_campaign_id"] == "c-77"


def test_plan_upgrades_attributed_order_missing_adset() -> None:
    """Orden que YA tiene meta_ad_id (forward-stamping / backfill previo)
    pero sin segmento → action `adset_upgrade` con SOLO los campos de adset
    (no re-escribe la atribución existente). Idempotente: si ya tiene
    meta_adset_id → skip como siempre."""
    orders = [
        {"id": "draft_old", "metadata": {"meta_ad_id": "AD_1", "meta_campaign_id": "c-77"}},
        {"id": "draft_done", "metadata": {"meta_ad_id": "AD_1", "meta_adset_id": "ADSET_A"}},
    ]
    plan = plan_order_patches(
        orders,
        {},
        {"AD_1": "c-77"},
        [],
        ad_to_adset={"AD_1": ("ADSET_A", "Hombres 25-45")},
    )
    by_id = {p["order_id"]: p for p in plan}
    assert by_id["draft_old"]["action"] == "adset_upgrade"
    assert by_id["draft_old"]["patch"] == {
        "meta_adset_id": "ADSET_A",
        "meta_adset_name": "Hombres 25-45",
    }
    assert by_id["draft_done"]["action"] == "skip"


def test_plan_skips_canceled_orders() -> None:
    # Una orden CANCELADA no es una venta: no recibe atribución (además Medusa
    # rechaza updates sobre canceladas — caso real: order #1 de prueba, 2026-07-09).
    orders = [
        {"id": "draft_dead", "metadata": {}, "status": "canceled"},
        {"id": "draft_ok", "metadata": {}, "status": "completed"},
    ]
    plan = plan_order_patches(orders, {}, {}, ["c-1"])
    assert [p["action"] for p in plan] == ["skip", "seeded"]
