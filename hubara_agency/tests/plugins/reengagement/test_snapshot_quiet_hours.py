"""Prefiltro de quiet hours en el snapshot builder (política 2026-08-04).

De noche (hora LOCAL del cliente) no se toca a nadie — eso ya lo garantiza
el gate `check_reengagement_policy` al ejecutar. Este prefiltro agrega la
parte de COSTO: si todas las sesiones están en quiet hours, el snapshot sale
vacío y el ciclo NO despierta la caja EC2 t3.large (~50% de los despertares
eran nocturnos). Defensa en profundidad intacta: gate re-valida al enviar.

El contador `prefiltered["quiet_hours"]` hace visible lo excluido (nunca un
cap silencioso).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from src.plugins.reengagement.agent.cycle.activities import (
    build_reengagement_snapshot_activity,
)
from src.plugins.reengagement.agent.cycle.use_cases import (
    build_snapshot_from_sessions,
)

ONE_HOUR_MS = 60 * 60 * 1000


def _seed(vault: Path, session_id: str, data: dict) -> None:
    d = vault / session_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(data), encoding="utf-8")


def _dormant_open_window(now_ms: int) -> dict:
    """Metadata que SIN quiet hours entra al snapshot (CSW abierta + dormido)."""
    return {
        "tag": "INTERESADO",
        "service_window_expires_at_ms": now_ms + ONE_HOUR_MS,
        "ctwa_window_expires_at_ms": now_ms - ONE_HOUR_MS,
        "last_inbound_at_ms": now_ms - 5 * ONE_HOUR_MS,
    }


@pytest.mark.asyncio
async def test_de_noche_el_snapshot_sale_vacio_y_no_se_despierta_la_caja(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """Ventana permitida vacía (START=END=0 → siempre quiet): TODA sesión se
    prefiltra con razón `quiet_hours` → conversations vacío → el workflow
    skipea el dispatch (no se paga cold start)."""
    monkeypatch.setenv("WATCHDOG_QUIET_HOURS_START", "0")
    monkeypatch.setenv("WATCHDOG_QUIET_HOURS_END", "0")
    now_ms = int(time.time() * 1000)
    _seed(_isolate_vault_dir, "wa_+573001112233", _dormant_open_window(now_ms))
    _seed(
        _isolate_vault_dir,
        "wa_+573009998877",
        {**_dormant_open_window(now_ms), "tag": "CONFIRMADO_PAGO_PENDIENTE"},
    )

    env = ActivityEnvironment()
    snapshot = await env.run(build_reengagement_snapshot_activity)

    assert snapshot["conversations"] == []
    assert snapshot["prefiltered"].get("quiet_hours") == 2


@pytest.mark.asyncio
async def test_de_dia_las_mismas_sesiones_si_entran(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """Control: ventana 24h permitida → las mismas sesiones entran al seed."""
    monkeypatch.setenv("WATCHDOG_QUIET_HOURS_START", "0")
    monkeypatch.setenv("WATCHDOG_QUIET_HOURS_END", "24")
    now_ms = int(time.time() * 1000)
    _seed(_isolate_vault_dir, "wa_+573001112233", _dormant_open_window(now_ms))

    env = ActivityEnvironment()
    snapshot = await env.run(build_reengagement_snapshot_activity)

    assert [c["session_id"] for c in snapshot["conversations"]] == [
        "wa_+573001112233"
    ]
    assert "quiet_hours" not in snapshot["prefiltered"]


def test_quiet_checker_inyectable_es_puro():
    """El builder es puro (sin reloj/env): el checker viene inyectado por la
    activity. None = sin filtro (compat con los tests puros existentes)."""
    now_ms = 1_750_000_000_000
    sessions = [
        ("wa_dia", _dormant_open_window(now_ms)),
        ("wa_noche", _dormant_open_window(now_ms)),
    ]

    snapshot = build_snapshot_from_sessions(
        now_ms, sessions, quiet_checker=lambda sid: sid == "wa_noche"
    )

    assert [c["session_id"] for c in snapshot["conversations"]] == ["wa_dia"]
    assert snapshot["prefiltered"].get("quiet_hours") == 1
