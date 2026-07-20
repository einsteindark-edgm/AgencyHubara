"""Verbos ``package`` del CLI GraphAgents — superficie que consume Acktos Studio.

Mismos verbos y salida ``--json`` que el CLI hubara; ``--root`` apunta a un
GraphAgents root arbitrario (default: este). Se prueba por subprocess porque
el CLI resuelve imports relativos al cwd del GA root real.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.sdk.test_packaging import _mini_ga, _target_ga

GA_ROOT = Path(__file__).resolve().parents[2]


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "sdk.cli", *args],
        cwd=GA_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_package_plan_build_install_roundtrip(tmp_path: Path) -> None:
    origen = _mini_ga(tmp_path)
    out = tmp_path / "team.acktospkg"

    plan = _cli("package", "plan", "team", "--root", str(origen), "--json")
    assert plan.returncode == 0, plan.stderr
    payload = json.loads(plan.stdout)
    assert [u["id"] for u in payload["units"]] == ["scout", "team"]
    scout = payload["units"][0]
    assert scout["kind"] == "graphagent"
    assert "graphs/scout.py" in scout["files"]
    assert scout["requires"]["ports"] == ["llm"]

    build = _cli("package", "build", "team", "--root", str(origen), "-o", str(out))
    assert build.returncode == 0, build.stderr
    assert out.exists()

    destino = _target_ga(tmp_path)
    pi = _cli("package", "plan-install", str(out), "--root", str(destino), "--json")
    assert pi.returncode == 0, pi.stderr
    ipayload = json.loads(pi.stdout)
    assert {u["id"]: u["action"] for u in ipayload["units"]} == {
        "scout": "new",
        "team": "new",
    }

    inst = _cli("package", "install", str(out), "--root", str(destino), "--json")
    assert inst.returncode == 0, inst.stderr
    rpayload = json.loads(inst.stdout)
    assert sorted(rpayload["installed"]) == ["scout", "team"]
    assert (destino / "manifests" / "scout.agent.yaml").exists()
    assert (destino / "tools" / "my_tool" / "impl.py").exists()


def test_package_stage_para_paquete_combinado(tmp_path: Path) -> None:
    """F4: stagear unidades graphagent en un staging que sella el CLI hubara."""
    origen = _mini_ga(tmp_path)
    staging = tmp_path / "staging"
    res = _cli("package", "stage", "scout", "--root", str(origen), "--staging", str(staging))
    assert res.returncode == 0, res.stderr
    unit_yaml = staging / "units" / "graphagent-scout" / "unit.yaml"
    assert unit_yaml.exists()


def test_package_plan_agente_inexistente_exit_2(tmp_path: Path) -> None:
    origen = _mini_ga(tmp_path)
    res = _cli("package", "plan", "nope", "--root", str(origen))
    assert res.returncode == 2
    assert "nope" in res.stderr
