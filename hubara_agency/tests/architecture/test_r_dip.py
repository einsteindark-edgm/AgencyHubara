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
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


_HUBARA_ROOT: Path = Path(__file__).resolve().parents[2]


@pytest.mark.architecture
def test_r_dip_contracts_pass() -> None:
    """Invoca `uv run lint-imports` con cwd=hubara_agency/ y exige exit code 0."""
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
