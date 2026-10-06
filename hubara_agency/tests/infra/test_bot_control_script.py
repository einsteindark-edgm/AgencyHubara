"""`infra/scripts/bot_control.sh`: el control del bot nuevo desde una máquina
del equipo (2026-10-06). Corre `decisions/control.py` DENTRO del contenedor de
la API por SSM y muestra lo que respondió. Cada argumento pasa por una lista
blanca: nada llega a la caja como shell. Se prueba con un `aws` falso.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "infra" / "scripts" / "bot_control.sh"

_FAKE_AWS = """#!/bin/bash
printf '%s\\n' "$*" >> "$CALLS"
case "$*" in
  *send-command*) echo "cmd-123" ;;
  *"--query Status"*) echo "$STATUS" ;;
  *StandardOutputContent*) echo "Listo." ;;
  *StandardErrorContent*) echo "" ;;
esac
"""


@pytest.fixture
def box(tmp_path: Path) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "aws"
    fake.write_text(_FAKE_AWS)
    fake.chmod(0o755)
    calls = tmp_path / "calls.txt"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "CALLS": str(calls), "STATUS": "Success"}
    return {"env": env, "calls": calls}


def _run(box: dict, *args: str, status: str = "Success") -> subprocess.CompletedProcess:
    env = {**box["env"], "STATUS": status}
    return subprocess.run(["bash", str(_SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)


def _calls(box: dict) -> list[str]:
    return box["calls"].read_text().splitlines() if box["calls"].exists() else []


def test_it_runs_the_control_command_inside_the_api_container_by_ssm(box: dict) -> None:
    out = _run(box, "--por", "ana", "numeros", "agregar", "wa_573001234567")

    assert out.returncode == 0, out.stderr
    assert "Listo." in out.stdout
    [send] = [c for c in _calls(box) if "send-command" in c]
    assert "AWS-RunShellScript" in send
    assert "docker compose exec -T -w /app/hubara_agency api python -m src.plugins.chats.agent.sales.decisions.control" in send
    assert "--por ana numeros agregar wa_573001234567" in send


@pytest.mark.parametrize("bad", ["ana; rm -rf /", "$(id)", "a b", "`id`", "x|y", "<quien>"])
def test_an_argument_that_could_run_shell_on_the_box_is_refused(box: dict, bad: str) -> None:
    out = _run(box, "--por", bad, "estado")

    assert out.returncode == 2
    assert _calls(box) == []


def test_a_failed_command_on_the_box_fails_here_too(box: dict) -> None:
    out = _run(box, "--por", "ana", "capacidad", "baja", "canary", status="Failed")

    assert out.returncode != 0


def test_without_arguments_it_explains_how_to_use_it(box: dict) -> None:
    out = _run(box)

    assert out.returncode == 2 and "estado" in out.stdout + out.stderr
    assert _calls(box) == []
