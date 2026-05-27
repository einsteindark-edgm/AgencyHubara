"""Tests de `apply_rules` + `parse_rules_doc` — el motor de evaluación.

Cubre:
  * Parser: shapes válidos e inválidos (raises InvalidRulesDocError)
  * Tag matching: primera regla que matchea wins
  * Score weights: bins por feature, total ponderado
  * Letter mapping: mayor min_score que aplica
  * Reason hints: override de letter.reason si matchea
  * Edge cases: feature missing (None) en condiciones / bins
  * Casos canónicos: VIP, Recurrente, Nuevo, Frío, Estándar — fixtures.

R-DET: 100% puro, sin I/O. Sin fixtures de filesystem.
"""
from __future__ import annotations

import pytest

from src.platform.customer_scoring.port import CustomerFeatures
from src.platform.customer_scoring.rules import (
    InvalidRulesDocError,
    apply_rules,
    parse_rules_doc,
)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _make_features(**overrides) -> CustomerFeatures:
    """Helper — features default que las rules pueden consumir."""
    defaults = {
        "episodes_total": 0,
        "episodes_won": 0,
        "episodes_lost": 0,
        "episodes_partial": 0,
        "episodes_timeout": 0,
        "episodes_active": 0,
        "monetary_cop": 0,
        "recency_days": None,
        "frequency_total": 0,
        "lost_ratio": 0.0,
        "msgs_avg_to_close": None,
        "first_seen_days_ago": None,
        "last_purchase_at_ms": None,
        "last_purchase_order_id": None,
    }
    defaults.update(overrides)
    return CustomerFeatures(**defaults)


_MINIMAL_DOC = {
    "version": 1,
    "description": "minimal test doc",
    "tags": [],
    "score_weights": [],
    "score_letter": [
        {"min": 80, "letter": "A", "reason": "alto"},
        {"min": 60, "letter": "B", "reason": "medio"},
        {"min": 0, "letter": "C", "reason": "bajo"},
    ],
    "reason_hints": [],
}


# ----------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------


def test_parse_valid_minimal():
    doc = parse_rules_doc(_MINIMAL_DOC)
    assert doc.version == 1
    assert len(doc.score_letter) == 3
    # Verificamos sort descendente por min_score.
    assert doc.score_letter[0].min_score == 80


def test_parse_rejects_missing_version():
    with pytest.raises(InvalidRulesDocError, match="version"):
        parse_rules_doc({**_MINIMAL_DOC, "version": "v1"})


def test_parse_rejects_empty_score_letter():
    with pytest.raises(InvalidRulesDocError, match="score_letter"):
        parse_rules_doc({**_MINIMAL_DOC, "score_letter": []})


def test_parse_rejects_unknown_operator_in_when():
    bad = {
        **_MINIMAL_DOC,
        "tags": [{"name": "X", "when": {"monetary_cop": {"~~": 100}}}],
    }
    with pytest.raises(InvalidRulesDocError, match="operador"):
        parse_rules_doc(bad)


def test_parse_rejects_non_numeric_threshold():
    bad = {
        **_MINIMAL_DOC,
        "tags": [{"name": "X", "when": {"monetary_cop": {">=": "lots"}}}],
    }
    with pytest.raises(InvalidRulesDocError, match="numérico"):
        parse_rules_doc(bad)


# ----------------------------------------------------------------------
# apply_rules — tag matching
# ----------------------------------------------------------------------


def test_apply_tag_first_match_wins():
    """Si VIP y Recurrente ambas matchearían, VIP gana porque va primero."""
    doc = parse_rules_doc({
        **_MINIMAL_DOC,
        "tags": [
            {"name": "VIP", "when": {"monetary_cop": {">=": 1000000}}},
            {"name": "Recurrente", "when": {"frequency_total": {">=": 2}}},
        ],
    })
    score = apply_rules(
        _make_features(monetary_cop=2_000_000, frequency_total=5), doc
    )
    assert score.tag == "VIP"


def test_apply_tag_fallback_to_estandar():
    """Si ninguna rule matchea → 'Estándar'."""
    doc = parse_rules_doc({
        **_MINIMAL_DOC,
        "tags": [
            {"name": "VIP", "when": {"monetary_cop": {">=": 1000000}}},
        ],
    })
    score = apply_rules(_make_features(monetary_cop=500_000), doc)
    assert score.tag == "Estándar"


def test_apply_tag_when_with_none_feature_does_not_match():
    """recency_days=None NO debe matchear `recency_days >= 60`. Conservador:
    sin dato → no aplicamos la rule."""
    doc = parse_rules_doc({
        **_MINIMAL_DOC,
        "tags": [
            {"name": "Frío", "when": {"recency_days": {">=": 60}}},
        ],
    })
    score = apply_rules(_make_features(recency_days=None), doc)
    assert score.tag == "Estándar"  # NO Frío — recency_days es None


# ----------------------------------------------------------------------
# apply_rules — score weights
# ----------------------------------------------------------------------


def test_apply_score_picks_first_bin_under_upper():
    """value=100k cae en upper=200000 → 5 puntos. Total 5."""
    doc = parse_rules_doc({
        **_MINIMAL_DOC,
        "score_weights": [
            {
                "feature": "monetary_cop",
                "bins": [
                    {"upper": 200000, "points": 5},
                    {"upper": 1000000, "points": 25},
                    {"points": 40},
                ],
            },
        ],
    })
    score = apply_rules(_make_features(monetary_cop=100_000), doc)
    assert score.score_value == 5
    assert score.breakdown[0].points == 5
    assert score.breakdown[0].feature == "monetary_cop"


def test_apply_score_falls_back_to_cap_bin():
    """value=5M supera todos los upper → último bin (cap, sin upper) aplica."""
    doc = parse_rules_doc({
        **_MINIMAL_DOC,
        "score_weights": [
            {
                "feature": "monetary_cop",
                "bins": [
                    {"upper": 200000, "points": 5},
                    {"upper": 1000000, "points": 25},
                    {"points": 40},
                ],
            },
        ],
    })
    score = apply_rules(_make_features(monetary_cop=5_000_000), doc)
    assert score.score_value == 40


def test_apply_score_none_feature_uses_default_bin():
    """recency_days=None → debe usar el bin default (upper=None)."""
    doc = parse_rules_doc({
        **_MINIMAL_DOC,
        "score_weights": [
            {
                "feature": "recency_days",
                "bins": [
                    {"upper": 30, "points": 25},
                    {"points": 0},  # default
                ],
            },
        ],
    })
    score = apply_rules(_make_features(recency_days=None), doc)
    assert score.score_value == 0


def test_apply_score_multiple_weights_summed():
    """Total = suma de bins de todas las features."""
    doc = parse_rules_doc({
        **_MINIMAL_DOC,
        "score_weights": [
            {
                "feature": "monetary_cop",
                "bins": [{"upper": 1000000, "points": 25}, {"points": 40}],
            },
            {
                "feature": "frequency_total",
                "bins": [{"upper": 1, "points": 5}, {"points": 25}],
            },
        ],
    })
    score = apply_rules(
        _make_features(monetary_cop=500_000, frequency_total=5), doc
    )
    # 25 (monetary <= 1M) + 25 (frequency > 1) = 50
    assert score.score_value == 50
    assert len(score.breakdown) == 2


# ----------------------------------------------------------------------
# apply_rules — letter mapping
# ----------------------------------------------------------------------


def test_apply_letter_picks_highest_min_that_applies():
    """score=85 con [80→A, 60→B, 0→C] debe dar A."""
    doc = parse_rules_doc({
        **_MINIMAL_DOC,
        "score_weights": [
            {"feature": "monetary_cop", "bins": [{"upper": 100, "points": 85}]},
        ],
    })
    # forzamos score=85
    score = apply_rules(_make_features(monetary_cop=50), doc)
    assert score.score_value == 85
    assert score.score_letter == "A"


def test_apply_letter_picks_lowest_when_score_zero():
    doc = parse_rules_doc(_MINIMAL_DOC)
    score = apply_rules(_make_features(), doc)
    assert score.score_letter == "C"  # min=0 aplica con score=0


# ----------------------------------------------------------------------
# apply_rules — reason hints
# ----------------------------------------------------------------------


def test_apply_reason_hint_overrides_letter_reason():
    """Si un reason_hint matchea, sobreescribe la reason del letter."""
    doc = parse_rules_doc({
        **_MINIMAL_DOC,
        "reason_hints": [
            {
                "when": {"recency_days": {">=": 60}},
                "message": "Cliente frío — reactivar",
            },
        ],
    })
    score = apply_rules(_make_features(recency_days=90), doc)
    assert score.score_reason == "Cliente frío — reactivar"


def test_apply_reason_hint_falls_through_to_letter_when_no_match():
    doc = parse_rules_doc({
        **_MINIMAL_DOC,
        "reason_hints": [
            {
                "when": {"recency_days": {">=": 60}},
                "message": "Cliente frío",
            },
        ],
    })
    score = apply_rules(_make_features(recency_days=5), doc)
    assert score.score_reason == "bajo"  # del letter.reason de _MINIMAL_DOC


# ----------------------------------------------------------------------
# Canonical end-to-end con el rules.yaml real
# ----------------------------------------------------------------------


def _real_doc():
    """Carga el rules.yaml real del config — fixture canónico para validar
    el sistema entero, no solo el motor."""
    from pathlib import Path

    import yaml

    rules_path = (
        Path(__file__).resolve().parents[3]
        / "config" / "customer_scoring" / "rules.yaml"
    )
    raw = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    return parse_rules_doc(raw)


def test_canonical_vip_customer():
    """Cliente VIP prototípico: alto monetary + frequency + reciente."""
    doc = _real_doc()
    score = apply_rules(
        _make_features(
            monetary_cop=3_000_000,
            frequency_total=5,
            recency_days=3,
            lost_ratio=0.1,
            episodes_total=5,
            first_seen_days_ago=120,
        ),
        doc,
    )
    assert score.tag == "VIP"
    # 40 (>2M) + 25 (>3) + 25 (<7d) + 10 (<0.2) = 100
    assert score.score_value == 100
    assert score.score_letter == "A"


def test_canonical_nuevo_customer():
    """Cliente nuevo: 1 episodio, antigüedad <7d."""
    doc = _real_doc()
    score = apply_rules(
        _make_features(
            episodes_total=1,
            first_seen_days_ago=2,
            frequency_total=0,  # todavía no compró
            monetary_cop=0,
            recency_days=None,
            lost_ratio=0.0,
        ),
        doc,
    )
    assert score.tag == "Nuevo"


def test_canonical_frio_customer():
    """Cliente frío: recency alto + lost_ratio alto."""
    doc = _real_doc()
    score = apply_rules(
        _make_features(
            recency_days=120,
            lost_ratio=0.7,
            episodes_total=5,
            episodes_won=1,
            episodes_lost=3,
            monetary_cop=150_000,
            frequency_total=1,
        ),
        doc,
    )
    assert score.tag == "Frío"
    # Score muy bajo: 5 (monetary<200k) + 5 (freq=1) + 0 (recency>180→0) + (-10 lost>0.5)
    assert score.score_value <= 10
    assert score.score_letter == "D"


def test_canonical_estandar_customer():
    """Cliente que no matchea ninguna tag rule."""
    doc = _real_doc()
    score = apply_rules(
        _make_features(
            episodes_total=1,
            episodes_won=1,
            frequency_total=1,
            monetary_cop=400_000,
            recency_days=20,
            lost_ratio=0.0,
            first_seen_days_ago=30,  # NO nuevo (>=7)
        ),
        doc,
    )
    assert score.tag == "Estándar"


def test_rules_version_persisted_in_score():
    """El CustomerScore debe llevar el rules_version del doc — auditoría."""
    doc = _real_doc()
    score = apply_rules(_make_features(), doc)
    assert score.rules_version == doc.version >= 1


def test_score_exposes_frequency_and_episodes_for_5th_kv():
    """5° KV del UI ('X compras de Y episodios') depende de que apply_rules
    pase frequency_total + episodes_total al CustomerScore, no solo deje los
    defaults a 0."""
    doc = _real_doc()
    score = apply_rules(
        _make_features(
            frequency_total=3,
            episodes_total=5,
            episodes_won=3,
            episodes_lost=1,
            episodes_partial=1,
        ),
        doc,
    )
    assert score.frequency_total == 3
    assert score.episodes_total == 5
