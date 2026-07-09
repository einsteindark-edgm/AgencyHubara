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
