"""`seed_test_ctwa_sessions --clean`: las sesiones sintéticas salen del vault
a una CUARENTENA (`<vault>/_quarantine/seeded-<ts>/`), no a /dev/null.

El operador pidió limpiar la audiencia de marketing de los números falsos de
desarrollo ("[seed] mensaje 0"). Mover en vez de borrar: los readers del vault
solo miran `wa_*` de primer nivel (la cuarentena es invisible) y un error se
revierte moviendo la carpeta de vuelta.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from src.sdk.connectorkit import FilesystemAttributionStore


def _session(vault: Path, sid: str, metadata: dict, history: list[str] | None = None) -> None:
    d = vault / sid
    d.mkdir(parents=True)
    (d / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    if history:
        (d / "sessions").mkdir()
        (d / "sessions" / f"{sid}.jsonl").write_text(
            "\n".join(json.dumps({"role": "user", "content": c}) for c in history) + "\n",
            encoding="utf-8",
        )


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    _session(vault, "wa_573000000004", {"seeded_test": True, "tag": "INTERESADO"},
             ["[seed] mensaje 0", "[seed] mensaje 1"])
    # Sembrada sin el marker (versión vieja del script): se reconoce por el texto.
    _session(vault, "wa_573000000005", {"tag": "INTERESADO"}, ["[seed] mensaje 0"])
    # Cliente real que MENCIONA la palabra seed: no es sintética.
    _session(vault, "wa_573001234567", {"tag": "COMPRA_EXITOSA"},
             ["hola, quiero la vela seed de lavanda"])
    _session(vault, "wa_573002223344", {"tag": "INTERESADO"})
    (vault / "_campaigns").mkdir()
    return vault


def test_find_seed_sessions_reconoce_marker_y_texto_seed() -> None:
    from scripts.seed_test_ctwa_sessions import find_seed_sessions

    with tempfile.TemporaryDirectory() as tmp:
        vault = _vault(Path(tmp))
        found = sorted(p.name for p in find_seed_sessions(vault))
    assert found == ["wa_573000000004", "wa_573000000005"]


def test_dry_run_no_toca_nada() -> None:
    from scripts.seed_test_ctwa_sessions import quarantine_seed_sessions

    with tempfile.TemporaryDirectory() as tmp:
        vault = _vault(Path(tmp))
        result = quarantine_seed_sessions(vault, apply=False)
        assert result.found == 2
        assert result.moved == 0
        assert (vault / "wa_573000000004").exists()
        assert not (vault / "_quarantine").exists()


def test_apply_mueve_a_cuarentena_y_los_readers_dejan_de_verlas() -> None:
    from scripts.seed_test_ctwa_sessions import quarantine_seed_sessions

    with tempfile.TemporaryDirectory() as tmp:
        vault = _vault(Path(tmp))
        result = quarantine_seed_sessions(vault, apply=True, now_ms=1_758_500_000_000)
        assert result.found == 2 and result.moved == 2
        assert result.target_dir == vault / "_quarantine" / "seeded-20250922T001320Z"
        # Se fueron enteras (metadata + historial), reversible con un mv.
        moved = result.target_dir / "wa_573000000004"
        assert (moved / "metadata.json").exists()
        assert (moved / "sessions" / "wa_573000000004.jsonl").exists()
        assert not (vault / "wa_573000000004").exists()
        assert not (vault / "wa_573000000005").exists()
        # Las reales siguen ahí y los readers del vault no ven la cuarentena.
        ids = sorted(s.session_id for s in FilesystemAttributionStore(vault).scan_sessions())
        assert ids == ["wa_573001234567", "wa_573002223344"]
        # Idempotente: una segunda pasada no encuentra nada.
        again = quarantine_seed_sessions(vault, apply=True, now_ms=1_758_500_000_000)
        assert (again.found, again.moved) == (0, 0)
