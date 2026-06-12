"""Sampler del vault → eventos del dashboard (F1, auditoría frontend).

El sampler reemplaza al viejo `/stream` (snapshot cada 2.5s por cliente):
stat-ea metadata.json + history.jsonl y deriva QUÉ cambió. Estos tests cubren
la parte pura: muestreo de estado + diff → eventos, con un vault sintético en
tmp_path (las funciones aceptan `vault_dir` explícito — no tocan el real).
"""

import json
import os
from pathlib import Path

from src.plugins.chats.api.dashboard import _diff_to_events, _sample_vault_state


def _mk_session(
    vault: Path,
    sid: str,
    metadata: dict | None = None,
    with_history: bool = False,
) -> Path:
    session = vault / sid
    session.mkdir(parents=True)
    if metadata is not None:
        (session / "metadata.json").write_text(
            json.dumps(metadata), encoding="utf-8"
        )
    if with_history:
        (session / "sessions").mkdir()
        (session / "sessions" / f"{sid}.jsonl").write_text("{}\n", encoding="utf-8")
    return session


def test_empty_vault_yields_empty_state(tmp_path: Path) -> None:
    assert _sample_vault_state(tmp_path / "missing") == {}
    assert _sample_vault_state(tmp_path) == {}


def test_identical_samples_produce_no_events(tmp_path: Path) -> None:
    _mk_session(tmp_path, "wa_111", {"tag": "NUEVO"}, with_history=True)
    s1 = _sample_vault_state(tmp_path)
    s2 = _sample_vault_state(tmp_path, prev=s1)

    changed_ids, orders_changed, eta_changed = _diff_to_events(s1, s2)
    assert changed_ids == []
    assert orders_changed is False
    assert eta_changed is False


def test_metadata_touch_marks_session_changed(tmp_path: Path) -> None:
    session = _mk_session(tmp_path, "wa_111", {"tag": "NUEVO"})
    s1 = _sample_vault_state(tmp_path)

    meta = session / "metadata.json"
    meta.write_text(json.dumps({"tag": "CALIFICADO"}), encoding="utf-8")
    os.utime(meta, (meta.stat().st_atime, meta.stat().st_mtime + 5))

    s2 = _sample_vault_state(tmp_path, prev=s1)
    changed_ids, orders_changed, eta_changed = _diff_to_events(s1, s2)
    assert changed_ids == ["wa_111"]
    # tag cambió pero ni registered_order ni eta_tracking → sin cross-domain.
    assert orders_changed is False
    assert eta_changed is False


def test_registered_order_change_emits_orders_changed(tmp_path: Path) -> None:
    session = _mk_session(tmp_path, "wa_222", {"tag": "NUEVO"})
    s1 = _sample_vault_state(tmp_path)

    meta = session / "metadata.json"
    meta.write_text(
        json.dumps(
            {"tag": "NUEVO", "registered_order": {"success": True, "order_id": "o1"}}
        ),
        encoding="utf-8",
    )
    os.utime(meta, (meta.stat().st_atime, meta.stat().st_mtime + 5))

    s2 = _sample_vault_state(tmp_path, prev=s1)
    changed_ids, orders_changed, eta_changed = _diff_to_events(s1, s2)
    assert changed_ids == ["wa_222"]
    assert orders_changed is True
    assert eta_changed is False


def test_eta_tracking_change_emits_eta_changed(tmp_path: Path) -> None:
    session = _mk_session(tmp_path, "wa_333", {"eta_tracking": {"orders": {}}})
    s1 = _sample_vault_state(tmp_path)

    meta = session / "metadata.json"
    meta.write_text(
        json.dumps({"eta_tracking": {"orders": {"o9": {"current_stage": "ready"}}}}),
        encoding="utf-8",
    )
    os.utime(meta, (meta.stat().st_atime, meta.stat().st_mtime + 5))

    s2 = _sample_vault_state(tmp_path, prev=s1)
    changed_ids, orders_changed, eta_changed = _diff_to_events(s1, s2)
    assert changed_ids == ["wa_333"]
    assert orders_changed is False
    assert eta_changed is True


def test_new_and_removed_sessions_are_changes(tmp_path: Path) -> None:
    _mk_session(tmp_path, "wa_a", {"tag": "NUEVO"})
    s1 = _sample_vault_state(tmp_path)

    _mk_session(tmp_path, "wa_b", {"tag": "NUEVO"})
    s2 = _sample_vault_state(tmp_path, prev=s1)
    changed_ids, _, _ = _diff_to_events(s1, s2)
    assert changed_ids == ["wa_b"]

    changed_ids, _, _ = _diff_to_events(s2, s1)  # simétrico: borrado
    assert changed_ids == ["wa_b"]


def test_mtime_cache_avoids_reparse(tmp_path: Path) -> None:
    """Si el mtime no cambió, las signatures se reutilizan del sample previo
    (el sampler corre cada 2.5s — no puede re-parsear todo el vault siempre)."""
    _mk_session(tmp_path, "wa_444", {"registered_order": {"success": True}})
    s1 = _sample_vault_state(tmp_path)
    s2 = _sample_vault_state(tmp_path, prev=s1)
    assert s2["wa_444"]["orders_sig"] == s1["wa_444"]["orders_sig"]
    assert s2 == s1


def test_corrupt_metadata_does_not_crash(tmp_path: Path) -> None:
    session = _mk_session(tmp_path, "wa_555", None)
    (session / "metadata.json").write_text("{no es json", encoding="utf-8")
    state = _sample_vault_state(tmp_path)
    assert state["wa_555"]["orders_sig"] == ""
