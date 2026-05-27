"""Tests de `compute_customer_features` — el aggregator RFM puro.

Cubre:
  * Metadata vacío → todos los counts en 0, timestamps None
  * Legacy fallback (sin episodes[]) — usa `tag` + `registered_order`
  * Episodes modernos: ganado / lost / partial / timeout / active
  * Monetary cross-ref con Medusa totals map
  * Recency: usa Medusa created_at si está, fallback a episode.closed_at_ms
  * Lost ratio: 0.0 cuando no hay cierres formales
  * Msgs avg: usa msgs_count_at_close - at_start si ambos están

R-DET: `now_ms` por DI, completamente determinístico.
"""
from __future__ import annotations

from src.platform.customer_scoring.features import compute_customer_features


_NOW = 1_900_000_000_000  # ms epoch sintético para tests
_DAY_MS = 24 * 60 * 60 * 1000


def test_features_empty_metadata():
    """Sin metadata, todos los counts deben ser 0 y timestamps None."""
    f = compute_customer_features({}, now_ms=_NOW)
    assert f.episodes_total == 0
    assert f.episodes_won == 0
    assert f.episodes_lost == 0
    assert f.monetary_cop == 0
    assert f.recency_days is None
    assert f.frequency_total == 0
    assert f.lost_ratio == 0.0
    assert f.msgs_avg_to_close is None
    assert f.first_seen_days_ago is None
    assert f.last_purchase_at_ms is None


def test_features_legacy_fallback_with_registered_order():
    """Sesión legacy: tag=COMPRA_EXITOSA + registered_order success → 1 episodio
    sintético ganado."""
    meta = {
        "tag": "COMPRA_EXITOSA",
        "registered_order": {
            "success": True,
            "order_id": "order_01HXX_LEGACY",
            "total_cop": 250_000,
        },
    }
    f = compute_customer_features(
        meta,
        now_ms=_NOW,
        medusa_order_totals_cop={"order_01HXX_LEGACY": 250_000},
    )
    assert f.episodes_total == 1
    assert f.episodes_won == 1
    assert f.episodes_lost == 0
    assert f.monetary_cop == 250_000
    assert f.frequency_total == 1
    assert f.last_purchase_order_id == "order_01HXX_LEGACY"


def test_features_legacy_no_close_no_episodes():
    """Sesión legacy SIN cierre formal y sin registered_order → 0 episodios.
    El status_history del legacy NO se cuenta como episodios."""
    meta = {
        "tag": "RETOMA_VENTA",
        "status_history": [
            {"tag": "RETOMA_VENTA", "timestamp": 1700000000.0},
        ],
    }
    f = compute_customer_features(meta, now_ms=_NOW)
    assert f.episodes_total == 0


def test_features_episodes_won_with_medusa_total():
    """Episode con order_id + COMPRA_EXITOSA → contribuye al monetary del
    Medusa map."""
    meta = {
        "episodes": [
            {
                "episode_id": "ep_001",
                "started_at_ms": _NOW - 30 * _DAY_MS,
                "closed_at_ms": _NOW - 25 * _DAY_MS,
                "closing_tag": "COMPRA_EXITOSA",
                "order_id": "order_X",
            },
        ],
    }
    f = compute_customer_features(
        meta,
        now_ms=_NOW,
        medusa_order_totals_cop={"order_X": 500_000},
        medusa_order_created_at_ms={"order_X": _NOW - 25 * _DAY_MS},
    )
    assert f.monetary_cop == 500_000
    assert f.episodes_won == 1
    assert f.recency_days == 25
    assert f.last_purchase_at_ms == _NOW - 25 * _DAY_MS


def test_features_episodes_mixed():
    """Caso típico: 3 episodes — 1 ganado, 1 rechazado, 1 confirmado-sin-datos."""
    meta = {
        "episodes": [
            {
                "episode_id": "ep_001",
                "started_at_ms": _NOW - 90 * _DAY_MS,
                "closed_at_ms": _NOW - 85 * _DAY_MS,
                "closing_tag": "COMPRA_EXITOSA",
                "order_id": "order_A",
            },
            {
                "episode_id": "ep_002",
                "started_at_ms": _NOW - 60 * _DAY_MS,
                "closed_at_ms": _NOW - 58 * _DAY_MS,
                "closing_tag": "RECHAZO",
            },
            {
                "episode_id": "ep_003",
                "started_at_ms": _NOW - 10 * _DAY_MS,
                "closed_at_ms": _NOW - 9 * _DAY_MS,
                "closing_tag": "CONFIRMADO_SIN_DATOS",
            },
        ],
    }
    f = compute_customer_features(
        meta,
        now_ms=_NOW,
        medusa_order_totals_cop={"order_A": 300_000},
    )
    assert f.episodes_total == 3
    assert f.episodes_won == 1
    assert f.episodes_lost == 1
    assert f.episodes_partial == 1
    assert f.monetary_cop == 300_000
    # lost_ratio = 1 lost / (1 won + 1 lost) = 0.5
    assert f.lost_ratio == 0.5
    assert f.first_seen_days_ago == 90
    assert f.recency_days == 85  # del COMPRA_EXITOSA


def test_features_active_episode_not_counted_in_won():
    """Episode sin closed_at_ms es ACTIVE — no cuenta como won/lost."""
    meta = {
        "episodes": [
            {
                "episode_id": "ep_001",
                "started_at_ms": _NOW - 5 * _DAY_MS,
                "closed_at_ms": None,
                "closing_tag": None,
            },
        ],
    }
    f = compute_customer_features(meta, now_ms=_NOW)
    assert f.episodes_total == 1
    assert f.episodes_active == 1
    assert f.episodes_won == 0
    assert f.episodes_lost == 0


def test_features_confirmado_pago_pendiente_does_not_inflate_monetary():
    """HU verificación humana de pago: episodio cerrado con
    CONFIRMADO_PAGO_PENDIENTE tiene order_id (orden en Medusa) PERO el
    pago NO está verificado todavía. NO debe contar como `episodes_won`
    ni inflar `monetary_cop` — el humano confirmará desde el dashboard.
    Cuenta como `episodes_partial`."""
    meta = {
        "episodes": [
            {
                "episode_id": "ep_001",
                "started_at_ms": _NOW - 1 * _DAY_MS,
                "closed_at_ms": _NOW - 1 * _DAY_MS + 3600_000,
                "closing_tag": "CONFIRMADO_PAGO_PENDIENTE",
                "order_id": "draft_pending_payment",
            },
        ],
    }
    f = compute_customer_features(
        meta,
        now_ms=_NOW,
        # Aunque el Medusa map tenga el monto, NO debe sumarlo al monetary
        # mientras el humano no confirme el pago.
        medusa_order_totals_cop={"draft_pending_payment": 99_000},
    )
    assert f.episodes_total == 1
    assert f.episodes_won == 0, (
        "CONFIRMADO_PAGO_PENDIENTE NO debe contar como won "
        "hasta que el humano verifique el pago"
    )
    assert f.episodes_lost == 0
    assert f.episodes_partial == 1, (
        "Debe registrarse como partial (semántica espejo de "
        "CONFIRMADO_SIN_DATOS)"
    )
    assert f.monetary_cop == 0, (
        "El monto NO debe sumarse hasta que el humano confirme el pago"
    )
    assert f.recency_days is None, (
        "Sin venta confirmada, no hay last_purchase_at_ms"
    )


def test_features_msgs_avg_computed_from_counts():
    """Si los episodes tienen msgs_count_at_start/close, msgs_avg debe ser
    el promedio."""
    meta = {
        "episodes": [
            {
                "episode_id": "ep_001",
                "started_at_ms": 1, "closed_at_ms": 2,
                "closing_tag": "COMPRA_EXITOSA",
                "msgs_count_at_start": 0,
                "msgs_count_at_close": 10,  # 10 msgs
            },
            {
                "episode_id": "ep_002",
                "started_at_ms": 3, "closed_at_ms": 4,
                "closing_tag": "RECHAZO",
                "msgs_count_at_start": 10,
                "msgs_count_at_close": 16,  # 6 msgs
            },
        ],
    }
    f = compute_customer_features(meta, now_ms=_NOW)
    # avg de [10, 6] = 8.0
    assert f.msgs_avg_to_close == 8.0


def test_features_order_id_without_medusa_total_is_won_but_zero_monetary():
    """Stub orders (HUB-*, AUDIT-*) que NO están en Medusa: cuentan como won
    (episode.order_id truthy) PERO no contribuyen al monetary (Medusa map no
    los tiene)."""
    meta = {
        "episodes": [
            {
                "episode_id": "ep_001",
                "started_at_ms": _NOW - 10 * _DAY_MS,
                "closed_at_ms": _NOW - 9 * _DAY_MS,
                "closing_tag": "COMPRA_EXITOSA",
                "order_id": "HUB-STUB-12345",
            },
        ],
    }
    f = compute_customer_features(
        meta, now_ms=_NOW,
        medusa_order_totals_cop={},  # vacío — Medusa no tiene este order
    )
    assert f.episodes_won == 1
    assert f.monetary_cop == 0  # sin total Medusa


def test_features_recency_uses_medusa_created_at_when_available():
    """recency_days debe preferir Medusa created_at sobre episode.closed_at_ms
    (más preciso)."""
    meta = {
        "episodes": [
            {
                "episode_id": "ep_001",
                "started_at_ms": _NOW - 100 * _DAY_MS,
                "closed_at_ms": _NOW - 100 * _DAY_MS,  # incorrecto
                "closing_tag": "COMPRA_EXITOSA",
                "order_id": "order_X",
            },
        ],
    }
    f = compute_customer_features(
        meta, now_ms=_NOW,
        medusa_order_totals_cop={"order_X": 100_000},
        medusa_order_created_at_ms={"order_X": _NOW - 5 * _DAY_MS},  # correcto
    )
    assert f.recency_days == 5  # del Medusa, no del episode
