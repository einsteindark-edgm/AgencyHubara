"""Reaper de temporal-test-server zombis — lógica pura + kill best-effort.

Ver el porqué completo en tests/test_temporal_test_server_reaper.py (docstring):
pytest matado a mitad deja vivos su `temporal-test-server-sdk-python-*` (y a
veces el propio pytest hijo), y con esos zombis el siguiente
`WorkflowEnvironment.start_time_skipping()` se cuelga para siempre.

Consumido por el fixture session-autouse de tests/conftest.py. La detección es
pura (testeable con líneas de ps falsas); solo `reap_stale_test_servers()`
toca el sistema, y es best-effort — nunca hace fallar la suite.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
from collections.abc import Callable

#: Marcador del binario que spawnea temporalio.testing — suficiente para no
#: matar jamás un proceso ajeno (el path incluye la versión del SDK).
_TEST_SERVER_MARKER = "temporal-test-server-sdk-python"

#: Un env de time-skipping legítimo vive lo que dura SU test (segundos). 20
#: minutos de vida = colgado con certeza; margen enorme sobre el cold-download
#: documentado (>2 min) del binario.
DEFAULT_MAX_AGE_SECONDS = 1200

_ETIME_RE = re.compile(
    r"^(?:(?P<days>\d+)-)?(?:(?P<hours>\d{1,2}):)?(?P<minutes>\d{1,2}):(?P<seconds>\d{2})$"
)


def parse_etime_seconds(etime: str) -> int | None:
    """Parsea el campo `etime` de ps (`MM:SS`, `HH:MM:SS`, `D-HH:MM:SS`)."""
    m = _ETIME_RE.match(etime.strip())
    if not m:
        return None
    days = int(m.group("days") or 0)
    hours = int(m.group("hours") or 0)
    minutes = int(m.group("minutes"))
    seconds = int(m.group("seconds"))
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def stale_test_server_pids(
    ps_lines: list[str],
    *,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    is_alive: Callable[[int], bool] | None = None,
) -> list[int]:
    """PIDs de test-servers zombis en la salida de
    ``ps -axo pid=,ppid=,etime=,command=``.

    Zombi = el comando es un `temporal-test-server-sdk-python-*` Y (su parent
    ya no existe — huérfano — O lleva vivo más de `max_age_seconds`).
    Cualquier línea malformada o proceso no relacionado se ignora.
    """
    alive = is_alive if is_alive is not None else _pid_alive
    stale: list[int] = []
    for line in ps_lines:
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        pid_s, ppid_s, etime_s, command = parts
        if _TEST_SERVER_MARKER not in command.split()[0]:
            continue
        try:
            pid = int(pid_s)
            ppid = int(ppid_s)
        except ValueError:
            continue
        age = parse_etime_seconds(etime_s)
        # ppid==1: en macOS/Linux los huérfanos se reparentan a launchd/init
        # (que está VIVO) — ppid==1 ES la señal de orfandad. Ningún flujo
        # legítimo spawnea el test-server desde PID 1.
        orphan = ppid == 1 or not alive(ppid)
        too_old = age is not None and age > max_age_seconds
        if orphan or too_old:
            stale.append(pid)
    return stale


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def reap_stale_test_servers(
    *, max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS
) -> list[int]:
    """Mata (SIGKILL) los test-servers zombis. Best-effort: cualquier error
    se ignora — el reaper jamás debe romper la suite. Devuelve los PIDs
    matados (para log)."""
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,etime=,command="],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except Exception:  # noqa: BLE001 — best-effort
        return []
    killed: list[int] = []
    for pid in stale_test_server_pids(
        out.splitlines(), max_age_seconds=max_age_seconds
    ):
        try:
            os.kill(pid, signal.SIGKILL)
            killed.append(pid)
        except Exception:  # noqa: BLE001 — carrera con exit natural / permisos
            continue
    return killed
