"""`quarantine_golden_sessions`: las sesiones `wa_golden_*` que el golden suite
dejó en el vault real (incidente 2026-09-23) salen a una CUARENTENA
(`<vault>/_quarantine/golden-<ts>/`), no a /dev/null.

Lo corre el operador en la caja (dry-run primero): mover en vez de borrar se
revierte con un `mv`, y los readers del vault solo miran `wa_*` de primer nivel.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.sdk.connectorkit import FilesystemAttributionStore


def _session(vault: Path, sid: str, metadata: dict) -> None:
    d = vault / sid
    d.mkdir(parents=True)
    (d / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    _session(vault, "wa_golden_apertura_fria_hola", {"tag": "INTERESADO"})
    _session(vault, "wa_golden_cierre_pago_transferencia", {"tag": "CONFIRMADO_PAGO_PENDIENTE"})
    _session(vault, "wa_573001234567", {"tag": "COMPRA_EXITOSA"})
    # No son sesiones golden de primer nivel: un archivo suelto y una ya en cuarentena.
    (vault / "wa_golden_notas.txt").write_text("x", encoding="utf-8")
    _session(vault / "_quarantine" / "golden-20260101T000000Z", "wa_golden_vieja", {})
    (vault / "_campaigns").mkdir()
    return vault


def test_find_golden_sessions_solo_directorios_wa_golden_de_primer_nivel(tmp_path: Path) -> None:
    from scripts.quarantine_golden_sessions import find_golden_sessions

    found = [p.name for p in find_golden_sessions(_vault(tmp_path))]

    assert found == ["wa_golden_apertura_fria_hola", "wa_golden_cierre_pago_transferencia"]


def test_cli_sin_apply_es_dry_run_y_con_apply_mueve(tmp_path: Path) -> None:
    from scripts.quarantine_golden_sessions import main

    vault = _vault(tmp_path)

    main(["--vault", str(vault)])
    assert (vault / "wa_golden_apertura_fria_hola").is_dir()
    assert [p.name for p in (vault / "_quarantine").iterdir()] == ["golden-20260101T000000Z"]

    main(["--vault", str(vault), "--apply"])
    assert not list(vault.glob("wa_golden_*/"))
    assert (vault / "wa_573001234567").is_dir()


def test_apply_mueve_a_cuarentena_reversible_e_idempotente(tmp_path: Path) -> None:
    from scripts.quarantine_golden_sessions import quarantine_golden_sessions

    vault = _vault(tmp_path)
    result = quarantine_golden_sessions(vault, apply=True, now_ms=1_790_000_000_000)

    assert (result.found, result.moved) == (2, 2)
    assert result.target_dir == vault / "_quarantine" / "golden-20260921T141320Z"
    # Se van enteras (reversible con un mv) y las reales siguen donde estaban.
    moved = result.target_dir / "wa_golden_cierre_pago_transferencia" / "metadata.json"
    assert json.loads(moved.read_text(encoding="utf-8")) == {"tag": "CONFIRMADO_PAGO_PENDIENTE"}
    assert (vault / "wa_golden_notas.txt").is_file()
    ids = [s.session_id for s in FilesystemAttributionStore(vault).scan_sessions()]
    assert ids == ["wa_573001234567"]
    # Una segunda pasada no encuentra nada.
    again = quarantine_golden_sessions(vault, apply=True, now_ms=1_790_000_000_000)
    assert (again.found, again.moved) == (0, 0)
