"""Scripts de la caja del laboratorio (plan del laboratorio, PR 6, §3.7).

La API de producción NO tiene red hacia la caja: la prende y le da la orden
por SSM (`AWS-RunShellScript`), que corre estos scripts. Candados del plan:

* **Una orden repetida no duplica la corrida.** El reintento de la activity
  que da la orden vuelve a correr `dispatch.sh` con el mismo id: la caja lo
  reconoce y no arranca otra.
* **Sin llaves de producción.** El `.env` del laboratorio sale SOLO de
  `/hubara-lab` (el rol de la caja no puede leer otra cosa).
* **La caja no se apaga con una corrida viva.** Una corrida pasa casi todo el
  tiempo esperando a los LLM, con la CPU baja: el autoapagado mira si hay un
  contenedor `lab-run-*` corriendo, no solo la CPU.

Se ejecutan con `aws`, `docker` y `vmstat` falsos en el PATH.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

LAB_SCRIPTS = Path(__file__).resolve().parents[3] / "infra" / "compose" / "lab"
IMAGE = "ghcr.io/einsteindark-edgm/agencyhubara:abc123"

_FAKE_AWS = """#!/bin/bash
echo "aws $*" >> "$CALLS"
if [[ "$*" == *"get-parameters-by-path"* ]]; then
  printf '/hubara-lab/DEEPSEEK_API_KEY\\tsk-deepseek\\n/hubara-lab/OPENROUTER_API_KEY\\tsk-or-lab\\n/hubara-lab/GHCR_PULL_TOKEN\\tghp_x\\n'
  exit 0
fi
if [[ "$*" == *"stop-instances"* ]]; then exit 0; fi
exit 0
"""

_FAKE_DOCKER = """#!/bin/bash
echo "docker $*" >> "$CALLS"
if [[ "$1" == "ps" ]]; then cat "$RUNNING" 2>/dev/null; exit 0; fi
exit 0
"""

_FAKE_CURL = """#!/bin/bash
case "$*" in
  *api/token*) echo "tok" ;;
  *instance-id*) echo "i-0lab" ;;
  *region*) echo "us-east-1" ;;
esac
"""

_FAKE_VMSTAT = """#!/bin/bash
printf 'procs\\n r b swpd free\\n 0 0 0 0 0 0 0 0 0 0 0 0 1 1 98 0 0\\n'
"""


@pytest.fixture
def box(tmp_path: Path) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {"aws": _FAKE_AWS, "docker": _FAKE_DOCKER, "curl": _FAKE_CURL, "vmstat": _FAKE_VMSTAT, "logger": "#!/bin/bash\n"}.items():
        f = bin_dir / name
        f.write_text(body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    home = tmp_path / "opt-lab"
    home.mkdir()
    (home / "box.env").write_text("AWS_REGION=us-east-1\nLAB_BUCKET=agencyhubara-lab-000000000000\nGHCR_OWNER=einsteindark-edgm\n")
    lab_root = tmp_path / "lab"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "LAB_HOME": str(home),
        "LAB_ROOT": str(lab_root),
        "CALLS": str(tmp_path / "calls.log"),
        "RUNNING": str(tmp_path / "running.txt"),
        "LAB_STATE": str(tmp_path / "state"),
    }
    return {"env": env, "home": home, "root": lab_root, "calls": tmp_path / "calls.log", "running": tmp_path / "running.txt"}


def _run(box: dict, script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(LAB_SCRIPTS / script), *args], env=box["env"], capture_output=True, text=True, timeout=30
    )


def _calls(box: dict) -> list[str]:
    return box["calls"].read_text().splitlines() if box["calls"].exists() else []


def test_dispatch_starts_the_stack_and_one_runner_for_the_run(box: dict) -> None:
    out = _run(box, "dispatch.sh", "run-20260923-a1", IMAGE)

    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "dispatched"
    runs = [c for c in _calls(box) if c.startswith("docker compose") and " run " in c]
    assert len(runs) == 1 and "--name lab-run-run-20260923-a1" in runs[0]
    assert (box["root"] / "runs" / "run-20260923-a1" / "dispatched").exists()


def test_repeated_dispatch_does_not_start_a_second_run(box: dict) -> None:
    _run(box, "dispatch.sh", "run-20260923-a1", IMAGE)

    again = _run(box, "dispatch.sh", "run-20260923-a1", IMAGE)

    assert again.returncode == 0
    assert again.stdout.strip().splitlines()[-1] == "already_dispatched"
    runs = [c for c in _calls(box) if c.startswith("docker compose") and " run " in c]
    assert len(runs) == 1


def test_lab_env_comes_only_from_the_lab_path(box: dict) -> None:
    _run(box, "dispatch.sh", "run-20260923-a1", IMAGE)

    [ssm] = [c for c in _calls(box) if "get-parameters-by-path" in c]
    assert "--path /hubara-lab " in ssm + " "
    env_file = (box["home"] / ".env").read_text()
    assert "OPENROUTER_API_KEY=sk-or-lab" in env_file
    assert f"HUBARA_IMAGE={IMAGE}" in env_file
    assert "LAB_BUCKET=agencyhubara-lab-000000000000" in env_file
    assert oct((box["home"] / ".env").stat().st_mode & 0o777) == "0o600"


@pytest.mark.parametrize(
    ("run_id", "image"),
    [
        ("../../etc", IMAGE),
        ("run; rm -rf /", IMAGE),
        ("run-20260923-a1", "docker.io/evil/image:latest"),
        ("run-20260923-a1", "ghcr.io/x/y:tag; curl evil"),
    ],
)
def test_dispatch_rejects_arguments_with_an_unexpected_shape(box: dict, run_id: str, image: str) -> None:
    out = _run(box, "dispatch.sh", run_id, image)

    assert out.returncode == 2
    assert not [c for c in _calls(box) if c.startswith("docker")]


def test_cancel_asks_the_runner_to_stop_and_is_idempotent(box: dict) -> None:
    _run(box, "dispatch.sh", "run-20260923-a1", IMAGE)

    first = _run(box, "cancel.sh", "run-20260923-a1")
    second = _run(box, "cancel.sh", "run-20260923-a1")

    assert (first.returncode, second.returncode) == (0, 0)
    assert first.stdout.strip() == "cancel_requested"
    assert (box["root"] / "runs" / "run-20260923-a1" / "CANCEL").exists()


def test_autostop_never_stops_the_box_while_a_run_is_alive(box: dict) -> None:
    box["running"].write_text("abc123\n")  # docker ps --filter name=lab-run- -q

    for _ in range(5):
        assert _run(box, "autostop.sh").returncode == 0

    assert not [c for c in _calls(box) if "stop-instances" in c]


def test_autostop_stops_after_ten_idle_minutes_without_runs(box: dict) -> None:
    box["env"]["IDLE_MIN"] = "10"

    _run(box, "autostop.sh")
    assert not [c for c in _calls(box) if "stop-instances" in c]
    _run(box, "autostop.sh")

    assert [c for c in _calls(box) if "stop-instances" in c]
