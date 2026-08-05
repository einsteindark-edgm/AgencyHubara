"""Fixtures del plugin reengagement.

`_no_quiet_hours` (autouse): la suite corre a cualquier hora del día y el
snapshot builder prefiltra por quiet hours con wall-clock — sin neutralizar
el horario, los tests de prefiltro/actividad fallarían corridos de noche
(mismo patrón que `test_check_reengagement_policy_activity.py`). Los tests
de quiet hours lo overridean explícitamente dentro del cuerpo del test.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_quiet_hours(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WATCHDOG_QUIET_HOURS_START", "0")
    monkeypatch.setenv("WATCHDOG_QUIET_HOURS_END", "24")
