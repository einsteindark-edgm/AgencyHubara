"""Smoke test del script — imports OK + dry-run no crashea."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def test_module_imports_cleanly():
    """Si el script tiene typo o import broken, falla aqui."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import scripts.create_catalog_sync_schedule",
        ],
        capture_output=True,
        text=True,
        cwd=_REPO,
    )
    assert result.returncode == 0, result.stderr


def test_dry_run_does_not_crash():
    """Dry-run no toca Temporal; solo imprime."""
    result = subprocess.run(
        [
            sys.executable,
            "scripts/create_catalog_sync_schedule.py",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        cwd=_REPO,
    )
    # Dry-run no conecta a Temporal — debe salir 0.
    assert result.returncode == 0, (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "dry-run" in result.stdout.lower()
