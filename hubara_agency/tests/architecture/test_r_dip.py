"""R-DIP — Dependency direction.

This test runs `lint-imports` (import-linter) as a subprocess. The 4 contracts
covering R-DIP live in `hubara_agency/.importlinter`:

  - `platform-no-agents`        — src.platform must not import agent packages
  - `agents-independent`        — agents must not import each other
  - `tools-no-temporal`         — tools/*.py must not import temporalio.*
  - `parsers-pure`              — parsers.py must not perform I/O

Why a subprocess: import-linter writes structured output that is easier to
read directly than re-implementing the contract engine. Failure mode: print
its full output as the assertion message so the operator can see exactly which
edge broke.

Cache hygiene: import-linter writes `.import_linter_cache/`. When source files
are renamed (e.g. moving a module from sales_whatsapp/tools/ to platform/tools/)
the cache can carry stale module-to-module edges and silence violations that
actually exist in the current source tree. We remove the cache before every
run so the contract evaluation always uses a fresh graph. (Discovered in the
premortem 2026-05-13: stale cache hid 2 cross-agent imports for several days.)
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


_HUBARA_ROOT: Path = Path(__file__).resolve().parents[2]
_CACHE_DIR: Path = _HUBARA_ROOT / ".import_linter_cache"


@pytest.mark.architecture
def test_r_dip_contracts_pass() -> None:
    """Invoca `uv run lint-imports` con cwd=hubara_agency/ y exige exit code 0."""
    # Garantizar grafo fresco. `ignore_errors=True` cubre el primer run
    # (cuando el cache aún no existe).
    shutil.rmtree(_CACHE_DIR, ignore_errors=True)

    result = subprocess.run(
        ["uv", "run", "lint-imports"],
        cwd=str(_HUBARA_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        "R-DIP violations — `lint-imports` reported broken contracts:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}\n"
        "Fix the offending import, or — if intentional — add an entry to "
        "`.importlinter` `ignore_imports` with a documented reason."
    )
