"""Seed sintético de conversaciones CTWA (histórico de prueba, 2026-07-09).

El plan de sesiones sintéticas debe producir EXACTAMENTE los estados del
funnel que promete — se verifica contra el clasificador REAL
(`classify_episode_state`), no contra strings mágicos. Todas marcadas
`seeded_test` (limpiables) y con teléfonos obviamente falsos.
"""
from __future__ import annotations

from src.plugins.ads.classification import classify_episode_state
from src.plugins.ads.synthetic_seed import build_seed_sessions

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
