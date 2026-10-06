"""El hook `affected-tests` del plugin hubara-dev certifica el paquete de
decisión que un agente acaba de editar (PAQUETES_DE_DECISION.md §6): una
llave inventada sale en el momento, con su código y su ruta, no en CI.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.platform.decisions.test_bundle_checker import BAJA, _baja, _write

REPO = Path(__file__).resolve().parents[3]
HOOK = REPO / "hubara-dev" / "hooks" / "scripts" / "affected-tests.py"


def _hook(file_path: Path) -> str:
    payload = json.dumps({"tool_input": {"file_path": str(file_path)}})
    out = subprocess.run(
        [sys.executable, str(HOOK)], input=payload, capture_output=True, text=True, timeout=180,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(REPO)},
    )
    return json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"] if out.stdout.strip() else ""


def _tree(tmp_path: Path) -> Path:
    return tmp_path / "hubara_agency" / "src" / "plugins" / "demo" / "decisions" / "bundles"


def test_editing_a_broken_capability_reports_the_error_right_away(tmp_path: Path) -> None:
    bundles = _tree(tmp_path)
    bundle_dir, _ = _write(bundles, {"baja": _baja(rule={"builtin": "is_opt_out"})})

    context = _hook(bundle_dir / "capabilities" / "baja.yaml")

    assert context.startswith("🔴")
    assert "DB004 capabilities/baja.yaml: rule.builtin" in context


def test_editing_a_valid_capability_says_it_compiles(tmp_path: Path) -> None:
    bundle_dir, _ = _write(_tree(tmp_path), {"baja": BAJA})

    context = _hook(bundle_dir / "bundle.yaml")

    assert context.startswith("🟢") and "tienda-ventas@1" in context
