"""Tests del gate `check_reengagement_policy_activity` (WS-B2, Window Strategist).

El guardrail de re-validación: ANTES de que el RemarketingWorkflow toque al
cliente, la central `decide_reengagement` re-decide con el estado REAL del
vault (metadata → LeadState vía `lead_state_from_metadata`). Cubre a TODOS los
dispatchers (agente GraphAgents, dashboard, transition INTERESADO).

Quiet hours: la autoridad es hubara-side — el gate suprime con
`suppress_reason="quiet_hours"` fuera del horario local permitido (mismos env
vars que el watchdog: WATCHDOG_QUIET_HOURS_START/END).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from src.platform.whatsapp.activities import check_reengagement_policy_activity

ONE_HOUR_MS = 60 * 60 * 1000


def _write_metadata(vault: Path, session_id: str, data: dict) -> None:
    d = vault / session_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture(autouse=True)
def _no_quiet_hours(monkeypatch: pytest.MonkeyPatch):
    # Default de la suite: horario permitido 24h (los tests de quiet hours
    # lo overridean explícitamente).
    monkeypatch.setenv("WATCHDOG_QUIET_HOURS_START", "0")
    monkeypatch.setenv("WATCHDOG_QUIET_HOURS_END", "24")


@pytest.mark.asyncio
async def test_human_owned_is_suppressed(_isolate_vault_dir: Path):
    now_ms = int(time.time() * 1000)
    _write_metadata(
        _isolate_vault_dir,
        "wa_573000000001",
        {
            "tag": "HUMANO",
            "service_window_expires_at_ms": now_ms + ONE_HOUR_MS,
            "ctwa_window_expires_at_ms": now_ms + ONE_HOUR_MS,
        },
    )
    decision = await ActivityEnvironment().run(
        check_reengagement_policy_activity, "wa_573000000001"
    )
    assert decision.allowed is False
    assert decision.suppress_reason == "human_owned"


@pytest.mark.asyncio
async def test_csw_open_allows_free_form(_isolate_vault_dir: Path):
    now_ms = int(time.time() * 1000)
    _write_metadata(
        _isolate_vault_dir,
        "wa_573000000002",
        {
            "tag": "INTERESADO",
            "service_window_expires_at_ms": now_ms + ONE_HOUR_MS,
            "ctwa_window_expires_at_ms": now_ms - ONE_HOUR_MS,
            # >30 min: fuera de la ventana customer_active (incidente
            # wa_573229041190) pero con CSW abierta — el caso free-form legítimo.
            "last_inbound_at_ms": now_ms - 31 * 60 * 1000,
        },
    )
    decision = await ActivityEnvironment().run(
        check_reengagement_policy_activity, "wa_573000000002"
    )
    assert decision.allowed is True
    assert decision.channel == "free_form"


@pytest.mark.asyncio
async def test_inbound_reciente_suprime_customer_active(_isolate_vault_dir: Path):
    # Carrera del incidente wa_573229041190: el cliente escribió segundos
    # antes de que el gate corriera — la conversación es de Sales.
    now_ms = int(time.time() * 1000)
    _write_metadata(
        _isolate_vault_dir,
        "wa_573000000005",
        {
            "tag": "INTERESADO",
            "service_window_expires_at_ms": now_ms + ONE_HOUR_MS,
            "ctwa_window_expires_at_ms": now_ms + ONE_HOUR_MS,
            "last_inbound_at_ms": now_ms - 2000,
        },
    )
    decision = await ActivityEnvironment().run(
        check_reengagement_policy_activity, "wa_573000000005"
    )
    assert decision.allowed is False
    assert decision.suppress_reason == "customer_active"


@pytest.mark.asyncio
async def test_phase_b_cold_is_suppressed(_isolate_vault_dir: Path):
    now_ms = int(time.time() * 1000)
    _write_metadata(
        _isolate_vault_dir,
        "wa_573000000003",
        {
            "tag": "NO_ETIQUETADO",
            "service_window_expires_at_ms": now_ms - ONE_HOUR_MS,
            "ctwa_window_expires_at_ms": now_ms - ONE_HOUR_MS,
        },
    )
    decision = await ActivityEnvironment().run(
        check_reengagement_policy_activity, "wa_573000000003"
    )
    assert decision.allowed is False
    assert decision.suppress_reason == "fase_b_cold_suppressed"


@pytest.mark.asyncio
async def test_quiet_hours_suppresses_even_with_open_window(
    _isolate_vault_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    # Horario permitido VACÍO → siempre es quiet hours (determinista sin
    # importar la hora del reloj del test).
    monkeypatch.setenv("WATCHDOG_QUIET_HOURS_START", "0")
    monkeypatch.setenv("WATCHDOG_QUIET_HOURS_END", "0")
    now_ms = int(time.time() * 1000)
    _write_metadata(
        _isolate_vault_dir,
        "wa_573000000004",
        {
            "tag": "INTERESADO",
            "service_window_expires_at_ms": now_ms + ONE_HOUR_MS,
            "ctwa_window_expires_at_ms": now_ms + ONE_HOUR_MS,
        },
    )
    decision = await ActivityEnvironment().run(
        check_reengagement_policy_activity, "wa_573000000004"
    )
    assert decision.allowed is False
    assert decision.suppress_reason == "quiet_hours"


@pytest.mark.asyncio
async def test_missing_metadata_fails_safe(_isolate_vault_dir: Path):
    decision = await ActivityEnvironment().run(
        check_reengagement_policy_activity, "wa_573000000099"
    )
    assert decision.allowed is False
    assert decision.suppress_reason == "metadata_missing"
