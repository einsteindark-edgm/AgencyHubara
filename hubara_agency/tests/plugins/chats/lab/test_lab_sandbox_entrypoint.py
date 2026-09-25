"""Entrypoint de UN caso en la caja (plan §3.5 candado 3, PR 11): un proceso
por caso, entorno del sandbox antes de importar la app, y no arranca si el
guard encuentra una llave de producción o un Temporal que no es el de la caja.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HUB = Path(__file__).resolve().parents[4]
MODULE = "src.plugins.chats.agent.sales_lab.sandbox.entrypoint"


def _run(tmp_path: Path, **env_over: str) -> subprocess.CompletedProcess:
    root = tmp_path / "lab"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("WHATSAPP_", "MEDUSA_", "META_", "TEMPORAL_"))}
    env.update({"PYTHONPATH": str(HUB), "LAB_ROOT": str(root), "TEMPORAL_URL": "temporal:7233",
                "TEMPORAL_NAMESPACE": "hubara-lab", "HUBARA_ENV": "lab"})
    env.update(env_over)
    case = tmp_path / "case.json"
    case.write_text(json.dumps({"case_id": "x"}), encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-m", MODULE, "--case", str(case), "--bench", str(tmp_path / "bench"),
         "--sandbox", str(root / "runs" / "run-x" / "A1" / "0" / "c1"), "--out", str(tmp_path / "out.json")],
        cwd=HUB, env=env, capture_output=True, text=True, timeout=120,
    )


def test_a_production_key_stops_the_case_before_importing_the_app(tmp_path: Path) -> None:
    proc = _run(tmp_path, WHATSAPP_ACCESS_TOKEN="EAAG-real")

    assert proc.returncode == 2
    assert "WHATSAPP_ACCESS_TOKEN" in proc.stderr
    assert not (tmp_path / "out.json").exists()


def test_temporal_cloud_stops_the_case(tmp_path: Path) -> None:
    proc = _run(tmp_path, TEMPORAL_API_KEY="cloud-key", TEMPORAL_URL="x.tmprl.cloud:7233")

    assert proc.returncode == 2
    assert "TEMPORAL" in proc.stderr


def test_a_sandbox_outside_the_lab_root_stops_the_case(tmp_path: Path) -> None:
    proc = _run(tmp_path, LAB_ROOT=str(tmp_path / "otro"))

    assert proc.returncode == 2
    assert "fuera de" in proc.stderr
