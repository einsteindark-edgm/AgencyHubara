"""Tests del resolver phone → session_id del endpoint customer-score.

Bug 2026-05-29: el phone que Medusa guarda en shipping_address suele venir
SIN country code (`3125671604`), mientras la sesión WhatsApp del vault usa el
número internacional completo (`wa_573125671604`). El mapeo ingenuo
`wa_<phone>` fallaba y "Historial cliente" siempre mostraba "Sin datos"
aunque el cliente tuviera episodios.

Estos tests fijan el comportamiento del `_resolve_session_id_for_phone` para
que no vuelva a romperse.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.plugins.orders.api import (
    _phone_match_key,
    _resolve_session_by_order_id,
    _resolve_session_id_for_phone,
)


def _make_session(vault: Path, name: str, metadata: dict | None = None) -> None:
    (vault / name).mkdir(parents=True, exist_ok=True)
    (vault / name / "metadata.json").write_text(
        json.dumps(metadata or {}), encoding="utf-8"
    )


# ----------------------------------------------------------------------
# _phone_match_key
# ----------------------------------------------------------------------


def test_match_key_last_10_digits():
    """La clave de match son los últimos 10 dígitos (móvil sin country code)."""
    assert _phone_match_key("3125671604") == "3125671604"
    assert _phone_match_key("573125671604") == "3125671604"
    assert _phone_match_key("+57 312-567-1604") == "3125671604"
    assert _phone_match_key("wa_573125671604") == "3125671604"


def test_match_key_short_number_kept_as_is():
    """Números < 10 dígitos se devuelven completos (caller decide confianza)."""
    assert _phone_match_key("12345") == "12345"


# ----------------------------------------------------------------------
# _resolve_session_id_for_phone
# ----------------------------------------------------------------------


def test_resolve_phone_without_country_code_matches_session_with_cc(tmp_path):
    """EL BUG: order phone sin cc (`3125671604`) debe resolver a la sesión
    con cc (`wa_573125671604`)."""
    _make_session(tmp_path, "wa_573125671604")
    assert (
        _resolve_session_id_for_phone(tmp_path, "3125671604")
        == "wa_573125671604"
    )


def test_resolve_phone_with_country_code_direct_match(tmp_path):
    """Phone que ya trae cc matchea directo (fast path)."""
    _make_session(tmp_path, "wa_573125671604")
    assert (
        _resolve_session_id_for_phone(tmp_path, "573125671604")
        == "wa_573125671604"
    )


def test_resolve_phone_with_plus_and_formatting(tmp_path):
    """Phone con `+`, espacios y guiones — todo normalizado a dígitos."""
    _make_session(tmp_path, "wa_573125671604")
    assert (
        _resolve_session_id_for_phone(tmp_path, "+57 312-567-1604")
        == "wa_573125671604"
    )


def test_resolve_session_dir_with_plus_prefix(tmp_path):
    """Algunas sesiones legacy tienen `+` en el nombre (`wa_+57...`). El
    scan debe matchearlas igual (normaliza dígitos de ambos lados)."""
    _make_session(tmp_path, "wa_+573125671604")
    assert (
        _resolve_session_id_for_phone(tmp_path, "3125671604")
        == "wa_+573125671604"
    )


def test_resolve_no_match_returns_none(tmp_path):
    """Phone que no matchea ninguna sesión → None (panel mostrará Sin datos)."""
    _make_session(tmp_path, "wa_573125671604")
    assert _resolve_session_id_for_phone(tmp_path, "3009999999") is None


def test_resolve_too_short_phone_returns_none(tmp_path):
    """Phone demasiado corto (< 7 dígitos) → None, evita falsos positivos."""
    _make_session(tmp_path, "wa_573125671604")
    assert _resolve_session_id_for_phone(tmp_path, "12345") is None


def test_resolve_picks_correct_session_among_many(tmp_path):
    """Con varias sesiones, resuelve la que matchea por los últimos 10 dígitos."""
    _make_session(tmp_path, "wa_573125671604")
    _make_session(tmp_path, "wa_5742040259")
    _make_session(tmp_path, "wa_573009876543")
    assert (
        _resolve_session_id_for_phone(tmp_path, "3009876543")
        == "wa_573009876543"
    )


def test_resolve_ignores_dirs_without_metadata(tmp_path):
    """Un dir `wa_*` sin metadata.json NO es un match válido."""
    (tmp_path / "wa_573125671604").mkdir()
    # sin metadata.json
    assert _resolve_session_id_for_phone(tmp_path, "3125671604") is None


def test_resolve_nonexistent_vault_returns_none(tmp_path):
    """Vault dir que no existe → None sin crashear."""
    missing = tmp_path / "does_not_exist"
    assert _resolve_session_id_for_phone(missing, "3125671604") is None


# ----------------------------------------------------------------------
# _resolve_session_by_order_id — reverse lookup (link canónico por episodios)
# ----------------------------------------------------------------------
#
# Bug 2026-05-29: dos órdenes del MISMO cliente con shipping phones distintos
# mostraban historiales distintos. El link canónico NO es el shipping phone
# (delivery metadata, puede diferir) sino el order_id registrado en los
# episodios de la sesión. Estos tests fijan el reverse lookup.


def test_reverse_lookup_finds_session_by_episode_order_id(tmp_path):
    """Una orden cuyo backend_id está en episodes[].order_id resuelve a esa
    sesión, sin importar el shipping phone."""
    _make_session(
        tmp_path,
        "wa_573125671604",
        {
            "episodes": [
                {"episode_id": "ep_001", "order_id": "order_AAA", "closed_at_ms": 1},
                {"episode_id": "ep_002", "order_id": "order_BBB", "closed_at_ms": 2},
            ]
        },
    )
    # Ambas órdenes del cliente resuelven a la misma sesión.
    assert _resolve_session_by_order_id(tmp_path, "order_AAA") == "wa_573125671604"
    assert _resolve_session_by_order_id(tmp_path, "order_BBB") == "wa_573125671604"


def test_reverse_lookup_finds_by_registered_order_legacy(tmp_path):
    """Sesión legacy sin episodes[] pero con registered_order.order_id."""
    _make_session(
        tmp_path,
        "wa_573125671604",
        {
            "registered_order": {"success": True, "order_id": "order_LEGACY"},
        },
    )
    assert (
        _resolve_session_by_order_id(tmp_path, "order_LEGACY")
        == "wa_573125671604"
    )


def test_reverse_lookup_no_match_returns_none(tmp_path):
    """order_id que no aparece en ningún episodio → None."""
    _make_session(
        tmp_path,
        "wa_573125671604",
        {"episodes": [{"episode_id": "ep_001", "order_id": "order_AAA"}]},
    )
    assert _resolve_session_by_order_id(tmp_path, "order_ZZZ") is None


def test_reverse_lookup_picks_right_session_among_many(tmp_path):
    """Con varias sesiones, devuelve la que tiene el order_id en sus episodios."""
    _make_session(
        tmp_path, "wa_573125671604",
        {"episodes": [{"episode_id": "ep_001", "order_id": "order_AAA"}]},
    )
    _make_session(
        tmp_path, "wa_5742040259",
        {"episodes": [{"episode_id": "ep_001", "order_id": "order_BBB"}]},
    )
    assert _resolve_session_by_order_id(tmp_path, "order_BBB") == "wa_5742040259"


def test_reverse_lookup_tolerates_corrupt_metadata(tmp_path):
    """Un metadata.json corrupto se ignora; sigue escaneando los demás."""
    (tmp_path / "wa_corrupt").mkdir()
    (tmp_path / "wa_corrupt" / "metadata.json").write_text(
        "{not valid json", encoding="utf-8"
    )
    _make_session(
        tmp_path, "wa_573125671604",
        {"episodes": [{"episode_id": "ep_001", "order_id": "order_AAA"}]},
    )
    assert _resolve_session_by_order_id(tmp_path, "order_AAA") == "wa_573125671604"


def test_reverse_lookup_nonexistent_vault_returns_none(tmp_path):
    missing = tmp_path / "nope"
    assert _resolve_session_by_order_id(missing, "order_AAA") is None
