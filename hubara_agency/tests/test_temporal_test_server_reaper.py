"""Guard del reaper de temporal-test-server zombis.

Bug local recurrente (memoria + caso 2026-07-06): corridas de pytest matadas
a mitad (harness timeout / SIGKILL al wrapper `uv run`) dejan vivos el pytest
hijo y su `temporal-test-server-sdk-python-*`. Con esos zombis vivos, el
SIGUIENTE `WorkflowEnvironment.start_time_skipping()` se cuelga PARA SIEMPRE
(el test 1 del archivo pasa, el test 2 nunca arranca) — y cada corrida
colgada que se mata agrega otro zombi: bola de nieve que deja "regresiones
corriendo horas".

`tests/_reaper.py` corta la bola al inicio de cada sesión pytest: detecta
test-servers zombis (huérfanos o más viejos que el umbral — un env legítimo
vive segundos) y los mata best-effort. Estos tests cubren la lógica pura de
detección; el fixture de conftest.py solo la invoca.
"""
from __future__ import annotations

from tests._reaper import parse_etime_seconds, stale_test_server_pids

# Formato de `ps -axo pid=,ppid=,etime=,command=` en macOS/Linux
_PS_LINES = [
    # test-server huérfano y joven — se mata igual (orphan). En macOS los
    # huérfanos se reparentan a PID 1 (launchd), que está VIVO: ppid==1 ES
    # la señal de orfandad, no la muerte del parent.
    "  101     1    03:12 /var/folders/x/T/temporal-test-server-sdk-python-1.25.0 59619",
    # test-server cuyo parent murió sin reparent visible (ppid apunta a un
    # pid inexistente) — también huérfano
    "  106   777    02:00 /var/folders/x/T/temporal-test-server-sdk-python-1.25.0 59700",
    # test-server con parent vivo pero VIEJO (1h44) — colgado, se mata
    "  202   900 01:44:18 /var/folders/x/T/temporal-test-server-sdk-python-1.25.0 59936",
    # test-server con parent vivo y joven — corrida legítima, NO tocar
    "  303   901    00:42 /var/folders/x/T/temporal-test-server-sdk-python-1.25.0 61188",
    # proceso no relacionado — NO tocar aunque sea viejo
    "  404     1 05-11:22:33 /usr/sbin/somethingelse temporal-ish",
    # test-server multi-día (formato D-HH:MM:SS) — se mata
    "  505   902 2-00:10:00 /var/folders/x/T/temporal-test-server-sdk-python-1.25.0 62001",
]

# PID 1 (launchd) SIEMPRE está vivo — el caso real de macOS que se nos
# escapó en la primera iteración: huérfano reparentado a 1 con parent "vivo".
_ALIVE = {1, 900, 901, 902}


def _fake_alive(pid: int) -> bool:
    return pid in _ALIVE


def test_parse_etime_formats():
    assert parse_etime_seconds("00:42") == 42
    assert parse_etime_seconds("03:12") == 192
    assert parse_etime_seconds("01:44:18") == 6258
    assert parse_etime_seconds("2-00:10:00") == 173400
    assert parse_etime_seconds("garbage") is None


def test_orphan_test_server_is_stale_even_if_young():
    pids = stale_test_server_pids(
        _PS_LINES, max_age_seconds=1200, is_alive=_fake_alive
    )
    # ppid==1 (reparentado a launchd) Y ppid muerto: ambos son orfandad
    assert 101 in pids
    assert 106 in pids


def test_old_test_server_is_stale_even_with_live_parent():
    pids = stale_test_server_pids(
        _PS_LINES, max_age_seconds=1200, is_alive=_fake_alive
    )
    assert 202 in pids
    assert 505 in pids


def test_young_test_server_with_live_parent_survives():
    pids = stale_test_server_pids(
        _PS_LINES, max_age_seconds=1200, is_alive=_fake_alive
    )
    assert 303 not in pids


def test_unrelated_processes_never_touched():
    pids = stale_test_server_pids(
        _PS_LINES, max_age_seconds=1200, is_alive=_fake_alive
    )
    assert 404 not in pids


def test_malformed_lines_are_ignored():
    pids = stale_test_server_pids(
        ["", "garbage line", "  abc def temporal-test-server-sdk-python-1 x"],
        max_age_seconds=1200,
        is_alive=_fake_alive,
    )
    assert pids == []
