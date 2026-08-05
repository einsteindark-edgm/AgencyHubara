"""Quiet hours con granularidad de minutos (política 2026-08-04).

El operador definió el corte nocturno en 21:30 (no 22:00): después de esa
hora la gente duerme y los toques generan bloqueos/reportes que degradan el
quality rating del número. `WATCHDOG_QUIET_HOURS_*` acepta ahora `HH:MM`
además de la hora entera (backwards-compatible: "22" == "22:00").

Boundaries (hora local del cliente): allowed = start <= t < end con
default 08:00–21:30 → 21:29 permitido, 21:30 bloqueado, 07:59 bloqueado,
08:00 permitido.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.platform.whatsapp.quiet_hours import is_quiet_hours_for_session

_CO_SESSION = "wa_+573001112233"
_UTC = ZoneInfo("UTC")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    # Los tests de defaults no deben depender del env de la máquina.
    monkeypatch.delenv("WATCHDOG_QUIET_HOURS_START", raising=False)
    monkeypatch.delenv("WATCHDOG_QUIET_HOURS_END", raising=False)


def _bogota(hour: int, minute: int) -> datetime:
    """Un datetime UTC cuyo equivalente Bogotá (UTC-5, sin DST) es HH:MM."""
    local = datetime(2026, 6, 1, hour, minute, tzinfo=ZoneInfo("America/Bogota"))
    return local.astimezone(_UTC)


class TestDefault0800To2130:
    def test_2129_bogota_permitido(self):
        assert is_quiet_hours_for_session(_CO_SESSION, _bogota(21, 29)) is False

    def test_2130_bogota_bloqueado(self):
        assert is_quiet_hours_for_session(_CO_SESSION, _bogota(21, 30)) is True

    def test_0759_bogota_bloqueado(self):
        assert is_quiet_hours_for_session(_CO_SESSION, _bogota(7, 59)) is True

    def test_0800_bogota_permitido(self):
        assert is_quiet_hours_for_session(_CO_SESSION, _bogota(8, 0)) is False

    def test_madrugada_bloqueado(self):
        assert is_quiet_hours_for_session(_CO_SESSION, _bogota(3, 0)) is True


class TestEnvHHMM:
    def test_override_con_minutos(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("WATCHDOG_QUIET_HOURS_START", "09:15")
        monkeypatch.setenv("WATCHDOG_QUIET_HOURS_END", "20:45")
        assert is_quiet_hours_for_session(_CO_SESSION, _bogota(9, 14)) is True
        assert is_quiet_hours_for_session(_CO_SESSION, _bogota(9, 15)) is False
        assert is_quiet_hours_for_session(_CO_SESSION, _bogota(20, 44)) is False
        assert is_quiet_hours_for_session(_CO_SESSION, _bogota(20, 45)) is True

    def test_hora_entera_sigue_valiendo(self, monkeypatch: pytest.MonkeyPatch):
        # Backwards-compat: el formato viejo (hora entera) no cambia.
        monkeypatch.setenv("WATCHDOG_QUIET_HOURS_START", "0")
        monkeypatch.setenv("WATCHDOG_QUIET_HOURS_END", "24")
        assert is_quiet_hours_for_session(_CO_SESSION, _bogota(23, 59)) is False
        assert is_quiet_hours_for_session(_CO_SESSION, _bogota(0, 0)) is False

    def test_ventana_vacia_es_siempre_quiet(self, monkeypatch: pytest.MonkeyPatch):
        # START == END == 0 → ventana permitida vacía. Lo usan los tests del
        # prefiltro del snapshot para simular "siempre de noche".
        monkeypatch.setenv("WATCHDOG_QUIET_HOURS_START", "0")
        monkeypatch.setenv("WATCHDOG_QUIET_HOURS_END", "0")
        assert is_quiet_hours_for_session(_CO_SESSION, _bogota(12, 0)) is True
