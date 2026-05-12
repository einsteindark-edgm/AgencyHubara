"""Smoke test del script trigger_catalog_sync.py — imports limpios + --help OK."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def test_module_imports_cleanly():
    """Si el script tiene typo o import roto, falla aqui."""
    result = subprocess.run(
        [sys.executable, "-c", "import scripts.trigger_catalog_sync"],
        capture_output=True,
        text=True,
        cwd=_REPO,
    )
    assert result.returncode == 0, result.stderr


def test_help_does_not_crash():
    """--help no toca Temporal y debe salir 0 con docstring."""
    result = subprocess.run(
        [
            sys.executable,
            "scripts/trigger_catalog_sync.py",
            "--help",
        ],
        capture_output=True,
        text=True,
        cwd=_REPO,
    )
    assert result.returncode == 0
    assert "trigger_catalog_sync" in result.stdout.lower() or "no-wait" in result.stdout
