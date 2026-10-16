"""`hubara_agency/scripts/ssm_run_in_api.sh`: corre un script de Python DENTRO
del contenedor del API de producción por SSM. La caja se busca por su tag Name
(como `bot_control.sh`; forge lo traduce al cliente nuevo): con un id escrito,
un clon en la misma cuenta corría código en la producción de la madre
(premortem 2026-10-09). Se prueba con un `aws` falso.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ssm_run_in_api.sh"

_FAKE_AWS = """#!/bin/bash
printf '%s\\n' "$*" >> "$CALLS"
case "$*" in
  *describe-instances*) echo "i-0cafe000000000001" ;;
  *send-command*) echo "cmd-123" ;;
  *"--query Status"*) echo "Success" ;;
  *StandardOutputContent*) echo "listo" ;;
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
    probe = tmp_path / "probe.py"
    probe.write_text("print('hola')\n")
    calls = tmp_path / "calls.txt"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "CALLS": str(calls)}
    env.pop("API_INSTANCE_ID", None)
    return {"env": env, "calls": calls, "probe": probe}


def _calls(box: dict) -> list[str]:
    return box["calls"].read_text().splitlines() if box["calls"].exists() else []


def test_the_script_names_no_box_of_its_own() -> None:
    assert re.search(r"(?<![a-z])i-0[0-9a-f]{16}", _SCRIPT.read_text(encoding="utf-8")) is None


def test_it_finds_the_app_box_by_its_name_tag(box: dict) -> None:
    out = subprocess.run(["bash", str(_SCRIPT), str(box["probe"])], env=box["env"], capture_output=True, text=True, timeout=60)

    assert out.returncode == 0, out.stderr
    [find] = [c for c in _calls(box) if "describe-instances" in c]
    assert re.search(r"Name=tag:Name,Values=[a-z0-9-]+-app\b", find), find
    [send] = [c for c in _calls(box) if "send-command" in c]
    assert "--instance-ids i-0cafe000000000001" in send


def test_a_given_box_skips_the_lookup(box: dict) -> None:
    env = {**box["env"], "API_INSTANCE_ID": "i-0beef000000000002"}

    out = subprocess.run(["bash", str(_SCRIPT), str(box["probe"])], env=env, capture_output=True, text=True, timeout=60)

    assert out.returncode == 0, out.stderr
    assert not [c for c in _calls(box) if "describe-instances" in c]
    [send] = [c for c in _calls(box) if "send-command" in c]
    assert "--instance-ids i-0beef000000000002" in send
