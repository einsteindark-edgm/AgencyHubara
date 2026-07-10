"""Seed sintético de conversaciones CTWA (histórico de prueba, 2026-07-09).

El plan de sesiones sintéticas debe producir EXACTAMENTE los estados del
funnel que promete — se verifica contra el clasificador REAL
(`classify_episode_state`), no contra strings mágicos. Todas marcadas
`seeded_test` (limpiables) y con teléfonos obviamente falsos.
"""
from __future__ import annotations

from src.plugins.ads.classification import classify_episode_state
from src.plugins.ads.synthetic_seed import (
    build_seed_sessions,
    plan_segment_spread,
)

_NOW_MS = 1_783_640_000_000  # 2026-07-09 aprox


def _classify(spec: dict) -> str:
    episode = spec["metadata"]["episodes"][-1]
    return classify_episode_state(
        episode,
        current_tag=spec["metadata"].get("tag"),
        total_msgs=spec["history_msgs"],
        last_inbound_ms=spec["last_inbound_ms"],
        now_ms=_NOW_MS,
    )


def test_seed_covers_the_open_funnel_states() -> None:
    specs = build_seed_sessions("120210000000000001", now_ms=_NOW_MS)
    states = [_classify(s) for s in specs]
    # cubre los estados NO cerrados (+ perdido/cotizado como cierres sin venta);
    # ganado NO — las ventas reales viven en Medusa (backfill), acá no se inventan.
    for expected in ("nuevo", "activo", "calificado", "cotizado", "perdido", "no_reply"):
        assert expected in states, f"falta {expected} en {states}"
    assert "ganado" not in states


def test_seed_sessions_are_marked_and_attributed() -> None:
    specs = build_seed_sessions("120210000000000001", now_ms=_NOW_MS)
    for s in specs:
        meta = s["metadata"]
        assert meta["seeded_test"] is True  # limpiable por marker
        assert meta["origin"]["source_id"] == "120210000000000001"
        assert meta["origin"]["channel"] == "ad"
        assert s["session_key"].startswith("wa_5730000009")  # teléfono falso
    # session keys únicos
    keys = [s["session_key"] for s in specs]
    assert len(keys) == len(set(keys))


def test_seed_timestamps_fall_in_recent_window() -> None:
    # Los episodios arrancan dentro de los últimos 7 días — el filtro de fecha
    # del dashboard (30d/7d) los tiene que agarrar.
    specs = build_seed_sessions("AD_X", now_ms=_NOW_MS)
    seven_days_ms = 7 * 24 * 60 * 60 * 1000
    for s in specs:
        started = s["metadata"]["episodes"][-1]["started_at_ms"]
        assert _NOW_MS - seven_days_ms <= started <= _NOW_MS


def test_won_sessions_mirror_medusa_orders() -> None:
    """Las compras de Medusa se espejan como conversaciones GANADAS del vault
    (pedido 2026-07-09): episodio cerrado con el order_id REAL + total real →
    el funnel suma ganado y el revenue del tablero coincide con Medusa."""
    from src.plugins.ads.synthetic_seed import build_won_sessions

    orders = [
        {"id": "order_B", "created_at": "2026-06-16T10:00:00.000Z",
         "metadata": {"total_cop": 150000}},
        {"id": "order_A", "created_at": "2026-06-15T14:30:00.000Z",
         "metadata": {"total_cop": 600000}},
    ]
    specs = build_won_sessions(orders, "AD_7")
    assert len(specs) == 2
    # deterministas: orden estable por id → mismas keys en re-runs (idempotente)
    assert [s["session_key"] for s in specs] == ["wa_573000000910", "wa_573000000911"]
    for s, (oid, total) in zip(specs, [("order_A", 600000), ("order_B", 150000)]):
        ep = s["metadata"]["episodes"][-1]
        assert ep["order_id"] == oid  # el order id REAL de Medusa (join coherente)
        assert ep["order_total_cop"] == total  # revenue del tablero = Medusa
        assert ep["closing_tag"] == "COMPRA_EXITOSA"
        assert ep["closed_at_ms"] is not None
        assert s["metadata"]["seeded_test"] is True
        assert s["metadata"]["origin"]["source_id"] == "AD_7"
        assert _classify(s) == "ganado"
    # las fechas salen del created_at de la orden (el filtro de fecha las respeta)
    assert specs[0]["metadata"]["episodes"][-1]["started_at_ms"] == 1781533800000


def test_won_sessions_skip_canceled_and_missing_total() -> None:
    from src.plugins.ads.synthetic_seed import build_won_sessions

    orders = [
        {"id": "order_dead", "status": "canceled", "created_at": "2026-06-01T00:00:00Z",
         "metadata": {"total_cop": 99}},
        {"id": "order_ok", "created_at": "2026-06-02T00:00:00Z",
         "metadata": {"total_cop": 17000}},
    ]
    specs = build_won_sessions(orders, "AD_7")
    assert [s["metadata"]["episodes"][-1]["order_id"] for s in specs] == ["order_ok"]


# --- reparto de sesiones seeded por segmento (2026-07-10) --------------------


def _seeded_session(key: str, source_id: str, *, order_id: str | None = None,
                    seeded: bool = True) -> tuple[str, dict]:
    ep = {"episode_id": "ep1", "order_id": order_id,
          "referral_snapshot": {"channel": "ad", "source_id": source_id}}
    meta = {
        "seeded_test": seeded,
        "origin": {"channel": "ad", "source_id": source_id},
        "last_touch": {"channel": "ad", "source_id": source_id},
        "episodes": [ep],
    }
    return key, meta


def test_segment_spread_round_robins_only_seeded_campaign_sessions() -> None:
    """Reparte las sesiones SEEDED de la campaña entre los ads destino
    (round-robin determinista por session_key). Sesiones reales (sin marker)
    o de OTRA campaña quedan intactas — cero contaminación."""
    campaign_ads = frozenset({"AD_OLD", "AD_SEG_B", "AD_SEG_C"})
    targets = ["AD_OLD", "AD_SEG_B", "AD_SEG_C"]
    sessions = [
        _seeded_session("wa_01", "AD_OLD"),
        _seeded_session("wa_02", "AD_OLD"),
        _seeded_session("wa_03", "AD_OLD", order_id="order_9"),
        _seeded_session("wa_real", "AD_OLD", seeded=False),
        _seeded_session("wa_otra", "AD_DE_OTRA_CAMPANA"),
    ]
    plan = plan_segment_spread(sessions, campaign_ads, targets)
    by_key = {p["session_key"]: p for p in plan}
    # solo las 3 seeded de la campaña entran al plan
    assert set(by_key) == {"wa_01", "wa_02", "wa_03"}
    # round-robin determinista (orden por session_key)
    assert by_key["wa_01"]["new_source_id"] == "AD_OLD"
    assert by_key["wa_02"]["new_source_id"] == "AD_SEG_B"
    assert by_key["wa_03"]["new_source_id"] == "AD_SEG_C"
    # la sesión con venta emite el patch de la orden (coherencia Medusa)
    assert by_key["wa_03"]["order_ids"] == ["order_9"]
    assert by_key["wa_01"]["order_ids"] == []


def test_segment_spread_order_ids_only_from_campaign_episodes() -> None:
    """Una sesión seeded puede tener un episodio re-atribuido a OTRA campaña
    (FU2). Su orden NO entra a `order_ids` — re-estamparla en Medusa
    falsificaría la atribución de esa otra campaña."""
    campaign_ads = frozenset({"AD_OLD"})
    key, meta = _seeded_session("wa_01", "AD_OLD", order_id="order_own")
    meta["episodes"].append(
        {
            "episode_id": "ep2",
            "order_id": "order_ajena",
            "referral_snapshot": {"channel": "ad", "source_id": "AD_DE_OTRA"},
        }
    )
    plan = plan_segment_spread([(key, meta)], campaign_ads, ["AD_NEW"])
    assert plan[0]["order_ids"] == ["order_own"]
    # y el snapshot ajeno queda intacto
    assert (
        plan[0]["metadata"]["episodes"][1]["referral_snapshot"]["source_id"]
        == "AD_DE_OTRA"
    )


def test_segment_spread_rewrites_origin_touch_and_snapshots() -> None:
    """El plan lleva la metadata YA reescrita: origin + last_touch +
    referral_snapshot de cada episodio apuntan al ad nuevo."""
    campaign_ads = frozenset({"AD_OLD"})
    key, meta = _seeded_session("wa_01", "AD_OLD")
    plan = plan_segment_spread([(key, meta)], campaign_ads, ["AD_NEW"])
    new_meta = plan[0]["metadata"]
    assert new_meta["origin"]["source_id"] == "AD_NEW"
    assert new_meta["last_touch"]["source_id"] == "AD_NEW"
    assert new_meta["episodes"][0]["referral_snapshot"]["source_id"] == "AD_NEW"
    # la metadata original NO se muta (plan puro)
    assert meta["origin"]["source_id"] == "AD_OLD"
