"""El golden suite que lanza el worker sales_eval corre en SU vault temporal,
aunque el worker tenga el vault real en el entorno.

Incidente 2026-09-23: `_golden_env()` copia el env del worker, que en prod trae
WORKSPACE_VAULT_DIR / EXOCLAW_STATE_DIR / CATALOG_SNAPSHOT_DIR apuntando al
volumen real (`infra/compose/render-env-from-ssm.sh`). El runner hacía
`setdefault` y no pisaba ninguno: las sesiones `wa_golden_<escenario>` caían en
el vault real (Chats, Orders, el eval online y los barridos las ven como
clientes), el runner leía el metadata del vault temp (vacío) y su `rmtree` de
aislamiento limpiaba el temp, no la sesión real.

El test recorre el camino real: el env que arma el worker → un proceso nuevo en
el mismo cwd → el setup de env del runner (su código top-level, sin `main`) →
dónde lee/escribe el código de producción.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from src.plugins.chats.agent.sales_eval.activities.eval_activities import (
    _GOLDEN_SCRIPT,
    _REPO_ROOT,
    _golden_env,
)

# run_name != "__main__": corre el setup de env del runner sin arrancar main().
_PROBE = """
import json, os, runpy, sys
from pathlib import Path

runpy.run_path(sys.argv[1], run_name="golden_env_probe")

from src.platform.catalog.paths import get_snapshot_dir
from src.platform.config import WORKSPACE_VAULT_DIR

print(json.dumps({
    "WORKSPACE_VAULT_DIR": str(WORKSPACE_VAULT_DIR),
    "CATALOG_SNAPSHOT_DIR": str(get_snapshot_dir()),
    "EXOCLAW_STATE_DIR": str(Path(os.environ["EXOCLAW_STATE_DIR"]).resolve()),
}))
"""


def test_golden_suite_con_el_vault_real_en_el_worker_usa_solo_el_vault_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "hubara_vault"
    # El env del worker sales_eval en prod (render-env-from-ssm.sh).
    monkeypatch.setenv("WORKSPACE_VAULT_DIR", str(real))
    monkeypatch.setenv("EXOCLAW_STATE_DIR", str(real / "agent_state"))
    monkeypatch.setenv("CATALOG_SNAPSHOT_DIR", str(real / "catalog"))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))  # el mkdtemp cae en tmp_path

    env = _golden_env()
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, str(_GOLDEN_SCRIPT)],
        cwd=str(_REPO_ROOT), env=env, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr

    vault = Path(env["GOLDEN_EVAL_VAULT"]).resolve()
    assert json.loads(proc.stdout.splitlines()[-1]) == {
        "WORKSPACE_VAULT_DIR": str(vault),
        "CATALOG_SNAPSHOT_DIR": str(vault / "catalog"),
        "EXOCLAW_STATE_DIR": str(vault / "agent_state"),
    }
