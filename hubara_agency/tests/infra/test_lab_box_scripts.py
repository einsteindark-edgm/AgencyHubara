"""Scripts de la caja del laboratorio (plan del laboratorio, PR 6, §3.7).

La API de producción NO tiene red hacia la caja: la prende y le da la orden
por SSM (`AWS-RunShellScript`), que corre estos scripts. Candados del plan:

* **Una orden repetida no duplica la corrida.** El reintento de la activity
  que da la orden vuelve a correr `dispatch.sh` con el mismo id: la caja lo
  reconoce y no arranca otra. Sin el candado de `flock` la orden no sigue.
* **Una corrida a la vez.** Si otra corrida sigue viva (el lanzador de
  producción la dio por perdida pero la caja no), la orden nueva responde
  `busy`: dos workers de imágenes distintas se robarían las tareas.
* **Sin llaves de producción.** Las llaves salen SOLO de `/hubara-lab`, y cada
  servicio recibe solo las suyas: el token de GHCR va a `docker login`, nunca a
  un `.env`.
* **El mismo LiteLLM que producción.** El `litellm_config.yaml` sale de la
  imagen de la corrida (la misma que corre producción), no de una copia del
  día en que se creó la caja.
* **La caja no se apaga con una corrida viva, pero una corrida colgada no la
  deja prendida para siempre.** Una corrida pasa casi todo el tiempo esperando
  a los LLM, con la CPU baja: el autoapagado mira si hay un contenedor
  `lab-run-*` corriendo, y lo detiene si lleva más de `LAB_MAX_RUN_HOURS`.

Se ejecutan con `aws`, `docker`, `flock`, `timeout` y `vmstat` falsos en el PATH.
"""
from __future__ import annotations

import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

LAB_SCRIPTS = Path(__file__).resolve().parents[3] / "infra" / "compose" / "lab"
IMAGE = "ghcr.io/einsteindark-edgm/agencyhubara:abc123"
RUN = "run-20260923-a1"

_FAKE_AWS = """#!/bin/bash
echo "aws $*" >> "$CALLS"
if [[ "$*" == *"get-parameters-by-path"* ]]; then
  printf '/hubara-lab/DEEPSEEK_API_KEY\\tsk-deepseek\\n/hubara-lab/OPENROUTER_API_KEY\\tsk-or-lab\\n/hubara-lab/GHCR_PULL_TOKEN\\tghp_x\\n'
  exit 0
fi
exit 0
"""

# `docker ps` lista los nombres de $RUNNING; `container inspect` lee "<nombre>
# <estado> <código>" de $CONTAINERS; `compose run -d --name X` crea X; la
# imagen trae el litellm_config de $IMAGE_CONFIG (si falta, `cat` falla).
_FAKE_DOCKER = """#!/bin/bash
echo "docker $*" >> "$CALLS"
case "$1" in
  ps) cat "$RUNNING" 2>/dev/null ;;
  login) cat > "$LOGIN_STDIN" ;;
  container)
    if [ "$2" = inspect ]; then
      line=$(grep "^${@: -1} " "$CONTAINERS" 2>/dev/null) || exit 1
      echo "${line#* }"
    fi ;;
  run)
    [ -n "$IMAGE_CONFIG" ] && [ -f "$IMAGE_CONFIG" ] || exit 1
    cat "$IMAGE_CONFIG" ;;
  compose)
    if [[ "$*" == *" run -d --name "* ]]; then
      name=$(echo "$*" | sed -E 's/.* --name ([^ ]+).*/\\1/')
      echo "$name running 0" >> "$CONTAINERS"
    fi ;;
esac
exit 0
"""

_FAKE_FLOCK = """#!/bin/bash
echo "flock $*" >> "$CALLS"
[ -n "$FLOCK_FAIL" ] && exit 1
exit 0
"""

_FAKE_TIMEOUT = """#!/bin/bash
echo "timeout $*" >> "$CALLS"
shift
exec "$@"
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
    fakes = {
        "aws": _FAKE_AWS, "docker": _FAKE_DOCKER, "flock": _FAKE_FLOCK, "timeout": _FAKE_TIMEOUT,
        "curl": _FAKE_CURL, "vmstat": _FAKE_VMSTAT, "logger": "#!/bin/bash\n",
    }
    for name, body in fakes.items():
        f = bin_dir / name
        f.write_text(body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    home = tmp_path / "opt-lab"
    home.mkdir()
    (home / "box.env").write_text("AWS_REGION=us-east-1\nLAB_BUCKET=agencyhubara-lab-000000000000\nGHCR_OWNER=einsteindark-edgm\n")
    image_config = tmp_path / "image-litellm_config.yaml"
    image_config.write_text("model_list:\n  - model_name: deepseek-flash\n")
    lab_root = tmp_path / "lab"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "LAB_HOME": str(home),
        "LAB_ROOT": str(lab_root),
        "CALLS": str(tmp_path / "calls.log"),
        "RUNNING": str(tmp_path / "running.txt"),
        "CONTAINERS": str(tmp_path / "containers.txt"),
        "LOGIN_STDIN": str(tmp_path / "login.txt"),
        "IMAGE_CONFIG": str(image_config),
        "LAB_STATE": str(tmp_path / "state"),
    }
    return {
        "env": env, "home": home, "root": lab_root, "calls": tmp_path / "calls.log", "running": tmp_path / "running.txt",
        "containers": tmp_path / "containers.txt", "login": tmp_path / "login.txt", "image_config": image_config,
    }


def _run(box: dict, script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(LAB_SCRIPTS / script), *args], env=box["env"], capture_output=True, text=True, timeout=30
    )


def _calls(box: dict) -> list[str]:
    return box["calls"].read_text().splitlines() if box["calls"].exists() else []


def _compose_runs(box: dict) -> list[str]:
    return [c for c in _calls(box) if c.startswith("docker compose") and " run " in c]


def _answer(out: subprocess.CompletedProcess) -> str:
    return out.stdout.strip().splitlines()[-1]


def test_dispatch_starts_the_stack_and_one_runner_for_the_run(box: dict) -> None:
    out = _run(box, "dispatch.sh", RUN, IMAGE)

    assert out.returncode == 0, out.stderr
    assert _answer(out) == "dispatched"
    runs = _compose_runs(box)
    assert len(runs) == 1 and f"--name lab-run-{RUN}" in runs[0]
    assert (box["root"] / "runs" / RUN / "dispatched").exists()


def test_repeated_dispatch_does_not_start_a_second_run(box: dict) -> None:
    _run(box, "dispatch.sh", RUN, IMAGE)

    again = _run(box, "dispatch.sh", RUN, IMAGE)

    assert again.returncode == 0
    assert _answer(again) == "already_dispatched"
    assert len(_compose_runs(box)) == 1


def test_a_finished_run_is_still_already_dispatched(box: dict) -> None:
    _run(box, "dispatch.sh", RUN, IMAGE)
    box["containers"].write_text(f"lab-run-{RUN} exited 0\n")

    assert _answer(_run(box, "dispatch.sh", RUN, IMAGE)) == "already_dispatched"


@pytest.mark.parametrize("container", ["", f"lab-run-{RUN} exited 137\n"])
def test_a_dispatched_run_whose_runner_is_gone_is_reported_lost(box: dict, container: str) -> None:
    """La caja se reinició (Temporal se borra en cada arranque) o el runner
    murió: decir `already_dispatched` dejaría a producción esperando avance que
    nunca llega. `lost` hace fallar la corrida con un error claro."""
    _run(box, "dispatch.sh", RUN, IMAGE)
    box["containers"].write_text(container)

    again = _run(box, "dispatch.sh", RUN, IMAGE)

    assert again.returncode == 0
    assert _answer(again) == "lost"
    assert len(_compose_runs(box)) == 1


def test_dispatch_answers_busy_while_another_run_is_alive(box: dict) -> None:
    box["running"].write_text("lab-run-run-20260923-zz\n")

    out = _run(box, "dispatch.sh", RUN, IMAGE)

    assert out.returncode == 0
    assert _answer(out) == "busy"
    assert not [c for c in _calls(box) if c.startswith("docker compose")]
    assert not (box["root"] / "runs" / RUN / "dispatched").exists()


def test_dispatch_does_not_go_on_without_the_lock(box: dict) -> None:
    box["env"]["FLOCK_FAIL"] = "1"

    out = _run(box, "dispatch.sh", RUN, IMAGE)

    assert out.returncode != 0
    assert not [c for c in _calls(box) if c.startswith("docker")]


def test_lab_keys_come_only_from_the_lab_path_and_each_service_gets_its_own(box: dict) -> None:
    _run(box, "dispatch.sh", RUN, IMAGE)

    [ssm] = [c for c in _calls(box) if "get-parameters-by-path" in c]
    assert "--path /hubara-lab " in ssm + " "
    litellm = (box["home"] / "litellm.env").read_text()
    worker = (box["home"] / "sales_lab.env").read_text()
    compose = (box["home"] / "compose.env").read_text()
    assert "DEEPSEEK_API_KEY=sk-deepseek" in litellm and "OPENROUTER_API_KEY=sk-or-lab" in litellm
    assert "LAB_BUCKET" not in litellm and "HUBARA_IMAGE" not in litellm
    assert "OPENROUTER_API_KEY=sk-or-lab" in worker  # Jev va directo a OpenRouter
    assert "LAB_BUCKET=agencyhubara-lab-000000000000" in worker
    assert compose.strip() == f"HUBARA_IMAGE={IMAGE}"
    for name in ("litellm.env", "sales_lab.env", "compose.env"):
        assert oct((box["home"] / name).stat().st_mode & 0o777) == "0o600", name


def test_the_ghcr_token_only_goes_to_docker_login(box: dict) -> None:
    _run(box, "dispatch.sh", RUN, IMAGE)

    assert box["login"].read_text().strip() == "ghp_x"
    for env_file in box["home"].glob("*.env"):
        assert "ghp_x" not in env_file.read_text(), env_file.name


def test_dispatch_installs_the_litellm_config_of_the_run_image(box: dict) -> None:
    out = _run(box, "dispatch.sh", RUN, IMAGE)

    assert out.returncode == 0, out.stderr
    assert (box["home"] / "litellm_config.yaml").read_text() == box["image_config"].read_text()
    assert any(
        c == f"docker run --rm --entrypoint cat {IMAGE} /app/exoclaw-temporal/litellm_config.yaml" for c in _calls(box)
    )
    # config nueva → LiteLLM se recrea (el bind-mount no ve el archivo reemplazado)
    assert [c for c in _calls(box) if c.startswith("docker compose") and " rm -sf litellm" in c]


def test_an_unchanged_config_does_not_restart_litellm(box: dict) -> None:
    (box["home"] / "litellm_config.yaml").write_text(box["image_config"].read_text())

    _run(box, "dispatch.sh", RUN, IMAGE)

    assert not [c for c in _calls(box) if " rm -sf litellm" in c]


def test_dispatch_refuses_an_image_without_litellm_config(box: dict) -> None:
    box["image_config"].unlink()

    out = _run(box, "dispatch.sh", RUN, IMAGE)

    assert out.returncode != 0
    assert "litellm_config" in out.stderr
    assert not _compose_runs(box)
    assert not (box["root"] / "runs" / RUN / "dispatched").exists()


def test_dispatch_frees_disk_before_pulling_a_new_image(box: dict) -> None:
    _run(box, "dispatch.sh", RUN, IMAGE)

    calls = _calls(box)
    prune = next(i for i, c in enumerate(calls) if c.startswith("docker image prune"))
    pull = next(i for i, c in enumerate(calls) if c.startswith("docker compose") and " pull " in c)
    assert prune < pull
    assert "--filter until=168h" in calls[prune]


@pytest.mark.parametrize(
    ("run_id", "image"),
    [
        ("../../etc", IMAGE),
        ("run; rm -rf /", IMAGE),
        (RUN, "docker.io/evil/image:latest"),
        (RUN, "ghcr.io/x/y:tag; curl evil"),
    ],
)
def test_dispatch_rejects_arguments_with_an_unexpected_shape(box: dict, run_id: str, image: str) -> None:
    out = _run(box, "dispatch.sh", run_id, image)

    assert out.returncode == 2
    assert not [c for c in _calls(box) if c.startswith("docker")]


def test_cancel_asks_the_runner_to_stop_and_is_idempotent(box: dict) -> None:
    _run(box, "dispatch.sh", RUN, IMAGE)

    first = _run(box, "cancel.sh", RUN)
    second = _run(box, "cancel.sh", RUN)

    assert (first.returncode, second.returncode) == (0, 0)
    assert first.stdout.strip() == "cancel_requested"
    assert (box["root"] / "runs" / RUN / "CANCEL").exists()


def test_autostop_never_stops_the_box_while_a_run_is_alive(box: dict) -> None:
    box["running"].write_text(f"lab-run-{RUN}\n")

    for _ in range(5):
        assert _run(box, "autostop.sh").returncode == 0

    assert not [c for c in _calls(box) if "stop-instances" in c]
    assert [c for c in _calls(box) if c.startswith("timeout") and " docker ps" in c], "docker ps sin timeout"


def test_autostop_stops_after_ten_idle_minutes_without_runs(box: dict) -> None:
    box["env"]["IDLE_MIN"] = "10"

    _run(box, "autostop.sh")
    assert not [c for c in _calls(box) if "stop-instances" in c]
    _run(box, "autostop.sh")

    assert [c for c in _calls(box) if "stop-instances" in c]


def test_autostop_stops_a_run_stuck_past_the_max_hours_and_then_the_box(box: dict) -> None:
    """Un runner colgado (deadlock, I/O bloqueado) dejaba la caja prendida para
    siempre: la corrida se detiene al pasar `LAB_MAX_RUN_HOURS` desde la orden."""
    box["env"] |= {"IDLE_MIN": "5", "LAB_MAX_RUN_HOURS": "12"}
    marker = box["root"] / "runs" / RUN / "dispatched"
    marker.parent.mkdir(parents=True)
    marker.write_text("2026-09-23T00:00:00Z\n")
    old = time.time() - 13 * 3600
    os.utime(marker, (old, old))
    box["running"].write_text(f"lab-run-{RUN}\n")

    assert _run(box, "autostop.sh").returncode == 0

    calls = _calls(box)
    assert [c for c in calls if f"docker stop -t 30 lab-run-{RUN}" in c]
    assert [c for c in calls if "stop-instances" in c]


def test_autostop_keeps_a_young_run_even_with_an_old_box(box: dict) -> None:
    box["env"] |= {"IDLE_MIN": "5", "LAB_MAX_RUN_HOURS": "12"}
    marker = box["root"] / "runs" / RUN / "dispatched"
    marker.parent.mkdir(parents=True)
    marker.write_text("2026-09-23T00:00:00Z\n")
    box["running"].write_text(f"lab-run-{RUN}\n")

    _run(box, "autostop.sh")

    assert not [c for c in _calls(box) if c.startswith("docker stop") or "stop-instances" in c]
