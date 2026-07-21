"""Tests for `check_watchdog_eligibility_activity` (HU-WA24H-001 Sprint 2).

Pure activity tests — no Temporal worker, no time-skipping. The activity
reads `metadata.json` via `FilesystemMetadataStore` and decides whether to
fire. We use the autouse `_isolate_vault_dir` fixture from conftest to
redirect `WORKSPACE_VAULT_DIR` to a tmp dir.

Coverage matrix:

  | scenario                              | env       | metadata shape                            | expected reason        |
  |---|---|---|---|
  | feature flag off                      | unset     | (anything)                                | "feature_flag_off"     |
  | feature flag on + no metadata         | on        | {}                                        | "no_active_episode"    |
  | active_route=humano                   | on        | active_route=humano                       | "active_route_humano"  |
  | no episodes list                      | on        | active_route=ventas, no episodes          | "no_active_episode"    |
  | last episode closed                   | on        | episodes[-1].closed_at_ms != null         | "no_active_episode"    |
  | episode_id mismatch (new episode)     | on        | active ep_002, scheduled for ep_001       | "episode_id_mismatch"  |
  | window not expiring soon (>30min)     | on        | service_window_expires > now+30min        | "window_not_expiring_soon" |
  | window already expired                | on        | service_window_expires < now              | "window_not_expiring_soon" (delta_ms <= 0 is <= 30min, but the activity rejects "future"-only ≥ 30min)? |
  | happy path (eligible)                 | on        | window in <30min, awaiting_quote stage    | eligible=True          |
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from src.platform.whatsapp.window import WATCHDOG_PRE_EXPIRY_MS
from src.plugins.chats.agent.remarketing.activities.watchdog_activities import (
    check_watchdog_eligibility_activity,
)


SESSION_ID = "wa_+57300testabc"
EPISODE_ID = "ep_001"


def _write_metadata(vault_dir: Path, session_id: str, data: dict) -> None:
    target = vault_dir / session_id / "metadata.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _base_metadata(*, now_ms: int, expires_in_ms: int = 25 * 60 * 1000) -> dict:
    """Metadata that should pass all checks by default. Tests adjust fields
    per-scenario."""
    return {
        "active_route": "ventas",
        "service_window_expires_at_ms": now_ms + expires_in_ms,
        "episodes": [
            {
                "episode_id": EPISODE_ID,
                "started_at_ms": now_ms - (23 * 60 * 60 * 1000),
                "closed_at_ms": None,
                "closing_tag": None,
            }
        ],
        "tag": "INTERESADO",
        "motivo": "el cliente dudó del precio",
    }


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feature_flag_off_returns_skipped(
    monkeypatch: pytest.MonkeyPatch,
    _isolate_vault_dir: Path,
) -> None:
    """Default (env var unset) → eligible=False, reason='feature_flag_off'."""
    monkeypatch.delenv("WATCHDOG_ENABLED", raising=False)
    _write_metadata(_isolate_vault_dir, SESSION_ID, _base_metadata(now_ms=int(time.time() * 1000)))

    result = await check_watchdog_eligibility_activity(SESSION_ID, EPISODE_ID)

    assert result.eligible is False
    assert result.reason == "feature_flag_off"
    assert result.resolved_template_name is None


@pytest.mark.asyncio
async def test_feature_flag_explicit_false_returns_skipped(
    monkeypatch: pytest.MonkeyPatch,
    _isolate_vault_dir: Path,
) -> None:
    monkeypatch.setenv("WATCHDOG_ENABLED", "false")
    _write_metadata(_isolate_vault_dir, SESSION_ID, _base_metadata(now_ms=int(time.time() * 1000)))

    result = await check_watchdog_eligibility_activity(SESSION_ID, EPISODE_ID)

    assert result.eligible is False
    assert result.reason == "feature_flag_off"


# ---------------------------------------------------------------------------
# Active route guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_route_humano_returns_skipped(
    monkeypatch: pytest.MonkeyPatch,
    _isolate_vault_dir: Path,
) -> None:
    monkeypatch.setenv("WATCHDOG_ENABLED", "true")
    now_ms = int(time.time() * 1000)
    md = _base_metadata(now_ms=now_ms)
    md["active_route"] = "humano"
    _write_metadata(_isolate_vault_dir, SESSION_ID, md)

    result = await check_watchdog_eligibility_activity(SESSION_ID, EPISODE_ID)

    assert result.eligible is False
    assert result.reason == "active_route_humano"


# ---------------------------------------------------------------------------
# Episode lifecycle guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_episodes_returns_skipped(
    monkeypatch: pytest.MonkeyPatch,
    _isolate_vault_dir: Path,
) -> None:
    monkeypatch.setenv("WATCHDOG_ENABLED", "1")
    now_ms = int(time.time() * 1000)
    md = _base_metadata(now_ms=now_ms)
    md["episodes"] = []
    _write_metadata(_isolate_vault_dir, SESSION_ID, md)

    result = await check_watchdog_eligibility_activity(SESSION_ID, EPISODE_ID)

    assert result.eligible is False
    assert result.reason == "no_active_episode"


@pytest.mark.asyncio
async def test_last_episode_closed_returns_skipped(
    monkeypatch: pytest.MonkeyPatch,
    _isolate_vault_dir: Path,
) -> None:
    monkeypatch.setenv("WATCHDOG_ENABLED", "1")
    now_ms = int(time.time() * 1000)
    md = _base_metadata(now_ms=now_ms)
    md["episodes"][-1]["closed_at_ms"] = now_ms - 1000
    md["episodes"][-1]["closing_tag"] = "COMPRA_EXITOSA"
    _write_metadata(_isolate_vault_dir, SESSION_ID, md)

    result = await check_watchdog_eligibility_activity(SESSION_ID, EPISODE_ID)

    assert result.eligible is False
    assert result.reason == "no_active_episode"


@pytest.mark.asyncio
async def test_episode_id_mismatch_returns_skipped(
    monkeypatch: pytest.MonkeyPatch,
    _isolate_vault_dir: Path,
) -> None:
    """A new episode opened between scheduling and firing — the watchdog is
    stale; skip."""
    monkeypatch.setenv("WATCHDOG_ENABLED", "1")
    now_ms = int(time.time() * 1000)
    md = _base_metadata(now_ms=now_ms)
    # The metadata says the active episode is ep_002 now.
    md["episodes"].append(
        {
            "episode_id": "ep_002",
            "started_at_ms": now_ms - 1000,
            "closed_at_ms": None,
            "closing_tag": None,
        }
    )
    # Mark ep_001 as closed (re-engagement scenario).
    md["episodes"][0]["closed_at_ms"] = now_ms - 2000
    _write_metadata(_isolate_vault_dir, SESSION_ID, md)

    # The watchdog was scheduled for ep_001 (this argument) but the active
    # episode is ep_002 — skip.
    result = await check_watchdog_eligibility_activity(SESSION_ID, EPISODE_ID)

    assert result.eligible is False
    assert result.reason == "episode_id_mismatch"


# ---------------------------------------------------------------------------
# Window timing guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_window_not_expiring_soon_returns_skipped(
    monkeypatch: pytest.MonkeyPatch,
    _isolate_vault_dir: Path,
) -> None:
    """If service_window_expires_at is more than 30min away, the watchdog
    is firing too early — defensive skip."""
    monkeypatch.setenv("WATCHDOG_ENABLED", "1")
    now_ms = int(time.time() * 1000)
    md = _base_metadata(now_ms=now_ms)
    # Window expires in 2 hours — > WATCHDOG_PRE_EXPIRY_MS (30 min) away.
    md["service_window_expires_at_ms"] = now_ms + (2 * 60 * 60 * 1000)
    _write_metadata(_isolate_vault_dir, SESSION_ID, md)

    result = await check_watchdog_eligibility_activity(SESSION_ID, EPISODE_ID)

    assert result.eligible is False
    assert result.reason == "window_not_expiring_soon"


@pytest.mark.asyncio
async def test_window_timing_check_respects_pre_expiry_env(
    monkeypatch: pytest.MonkeyPatch,
    _isolate_vault_dir: Path,
) -> None:
    """El gate defensivo de timing usa el lead CONFIGURABLE por env.

    Con `WATCHDOG_PRE_EXPIRY_MINUTES=120`, una ventana que cierra en ~90min
    NO es 'too early' (con la constante fija de 30min se skipeaba). Solo
    asertamos el gate de timing — la elegibilidad final depende además de
    quiet-hours (hora real) y la resolución de template."""
    monkeypatch.setenv("WATCHDOG_ENABLED", "1")
    monkeypatch.setenv("WATCHDOG_PRE_EXPIRY_MINUTES", "120")
    now_ms = int(time.time() * 1000)
    md = _base_metadata(now_ms=now_ms)
    md["service_window_expires_at_ms"] = now_ms + 90 * 60 * 1000  # 90min away
    _write_metadata(_isolate_vault_dir, SESSION_ID, md)

    result = await check_watchdog_eligibility_activity(SESSION_ID, EPISODE_ID)

    assert result.reason != "window_not_expiring_soon"


@pytest.mark.asyncio
async def test_window_missing_returns_skipped(
    monkeypatch: pytest.MonkeyPatch,
    _isolate_vault_dir: Path,
) -> None:
    monkeypatch.setenv("WATCHDOG_ENABLED", "1")
    now_ms = int(time.time() * 1000)
    md = _base_metadata(now_ms=now_ms)
    del md["service_window_expires_at_ms"]
    _write_metadata(_isolate_vault_dir, SESSION_ID, md)

    result = await check_watchdog_eligibility_activity(SESSION_ID, EPISODE_ID)

    assert result.eligible is False
    assert result.reason == "window_not_expiring_soon"


# ---------------------------------------------------------------------------
# Happy paths (template resolution)
# ---------------------------------------------------------------------------


def _pin_clock_at_10am_bogota(monkeypatch: pytest.MonkeyPatch) -> None:
    """Congela el reloj de la activity a las 10:00 America/Bogota (15:00 UTC).

    Anti-flakiness: los happy paths NO controlaban la hora y el gate de
    quiet hours usa wall-clock — corrida la suite antes de las 08:00 o
    después de las 22:00 hora Colombia, fallaban con
    `reason='outside_quiet_hours'` (visto 2026-07-07 a las 07:03).
    """
    from datetime import datetime, timezone

    from src.plugins.chats.agent.remarketing.activities import (
        watchdog_activities,
    )

    fixed = datetime(2026, 7, 7, 15, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(watchdog_activities, "_utc_now", lambda: fixed)


@pytest.mark.asyncio
async def test_eligible_resolves_awaiting_quote_template(
    monkeypatch: pytest.MonkeyPatch,
    _isolate_vault_dir: Path,
) -> None:
    """tag=INTERESADO + no registered_order → awaiting_quote stage → resolves
    `quote_ready_utility_v2`."""
    monkeypatch.setenv("WATCHDOG_ENABLED", "1")
    _pin_clock_at_10am_bogota(monkeypatch)
    now_ms = int(time.time() * 1000)
    md = _base_metadata(now_ms=now_ms)
    # window in 25min — within WATCHDOG_PRE_EXPIRY_MS (30min)
    md["service_window_expires_at_ms"] = now_ms + 25 * 60 * 1000
    _write_metadata(_isolate_vault_dir, SESSION_ID, md)

    result = await check_watchdog_eligibility_activity(SESSION_ID, EPISODE_ID)

    assert result.eligible is True
    assert result.reason is None
    assert result.resolved_template_name == "quote_ready_utility_v2"
    assert result.resolved_template_variables is not None
    # Variables should include the declared ones from the template spec —
    # y NUNCA el nombre: los templates v2 no saludan por nombre (incidente
    # 2026-07-21: slot vacío → Meta 131008).
    assert "product_or_quote_label" in result.resolved_template_variables
    assert "customer_first_name" not in result.resolved_template_variables


@pytest.mark.asyncio
async def test_eligible_resolves_awaiting_payment_with_registered_order(
    monkeypatch: pytest.MonkeyPatch,
    _isolate_vault_dir: Path,
) -> None:
    """registered_order present + episode NOT closed COMPRA_EXITOSA →
    awaiting_payment stage → resolves `payment_pending_utility_v2`."""
    monkeypatch.setenv("WATCHDOG_ENABLED", "1")
    _pin_clock_at_10am_bogota(monkeypatch)
    now_ms = int(time.time() * 1000)
    md = _base_metadata(now_ms=now_ms)
    md["service_window_expires_at_ms"] = now_ms + 20 * 60 * 1000
    md["registered_order"] = {"order_id": "ORD-1042", "success": True}
    _write_metadata(_isolate_vault_dir, SESSION_ID, md)

    result = await check_watchdog_eligibility_activity(SESSION_ID, EPISODE_ID)

    assert result.eligible is True
    assert result.resolved_template_name == "payment_pending_utility_v2"
    assert (
        result.resolved_template_variables is not None
        and result.resolved_template_variables.get("order_reference") == "ORD-1042"
    )


@pytest.mark.asyncio
async def test_quiet_hours_gate_skips_at_3am_bogota_deterministic(
    monkeypatch: pytest.MonkeyPatch,
    _isolate_vault_dir: Path,
) -> None:
    """Contraparte determinista del gate: con el reloj congelado a las
    03:00 Bogotá (08:00 UTC), una sesión por-lo-demás elegible se skipea
    con `outside_quiet_hours` — sin depender de la hora real de la corrida."""
    from datetime import datetime, timezone

    from src.plugins.chats.agent.remarketing.activities import (
        watchdog_activities,
    )

    monkeypatch.setenv("WATCHDOG_ENABLED", "1")
    fixed = datetime(2026, 7, 7, 8, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(watchdog_activities, "_utc_now", lambda: fixed)
    now_ms = int(time.time() * 1000)
    md = _base_metadata(now_ms=now_ms)
    md["service_window_expires_at_ms"] = now_ms + 25 * 60 * 1000
    _write_metadata(_isolate_vault_dir, SESSION_ID, md)

    result = await check_watchdog_eligibility_activity(SESSION_ID, EPISODE_ID)

    assert result.eligible is False
    assert result.reason == "outside_quiet_hours"


@pytest.mark.asyncio
async def test_pre_expiry_constant_is_30_minutes() -> None:
    """Sanity check on the constant used by the activity. If this changes,
    the eligibility window math above must move too."""
    assert WATCHDOG_PRE_EXPIRY_MS == 30 * 60 * 1000


# ---------------------------------------------------------------------------
# Quiet hours (HU-WA24H-001 pre-mortem F4.1)
# ---------------------------------------------------------------------------


class TestQuietHours:
    """Verifica que el watchdog NO dispara fuera del horario permitido del
    cliente (default 08:00-22:00 hora local). Critical para evitar quality
    rating drop por sends nocturnos."""

    def test_resolve_local_timezone_colombia(self):
        from src.plugins.chats.agent.remarketing.activities.watchdog_activities import (
            _resolve_local_timezone,
        )

        tz = _resolve_local_timezone("wa_+573001112233")
        assert str(tz) == "America/Bogota"

    def test_resolve_local_timezone_argentina(self):
        from src.plugins.chats.agent.remarketing.activities.watchdog_activities import (
            _resolve_local_timezone,
        )

        tz = _resolve_local_timezone("wa_+541112345678")
        assert str(tz) == "America/Argentina/Buenos_Aires"

    def test_resolve_local_timezone_mexico(self):
        from src.plugins.chats.agent.remarketing.activities.watchdog_activities import (
            _resolve_local_timezone,
        )

        tz = _resolve_local_timezone("wa_+525512345678")
        assert str(tz) == "America/Mexico_City"

    def test_resolve_local_timezone_unknown_country_falls_to_utc(self):
        from src.plugins.chats.agent.remarketing.activities.watchdog_activities import (
            _resolve_local_timezone,
        )

        # +99 no existe; longest-match no encuentra, default UTC
        tz = _resolve_local_timezone("wa_+999999999999")
        assert str(tz) == "UTC"

    def test_is_quiet_hours_returns_true_at_3am_colombia(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from src.plugins.chats.agent.remarketing.activities.watchdog_activities import (
            _is_quiet_hours_for_session,
        )

        # 08:00 UTC = 03:00 Colombia (UTC-5). Quiet hours.
        utc_3am_co = datetime(2026, 6, 1, 8, 0, 0, tzinfo=ZoneInfo("UTC"))
        assert _is_quiet_hours_for_session("wa_+573001112233", utc_3am_co) is True

    def test_is_quiet_hours_returns_false_at_10am_colombia(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from src.plugins.chats.agent.remarketing.activities.watchdog_activities import (
            _is_quiet_hours_for_session,
        )

        # 15:00 UTC = 10:00 Colombia (UTC-5). Allowed.
        utc_10am_co = datetime(2026, 6, 1, 15, 0, 0, tzinfo=ZoneInfo("UTC"))
        assert _is_quiet_hours_for_session("wa_+573001112233", utc_10am_co) is False

    def test_is_quiet_hours_returns_true_at_23_colombia(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from src.plugins.chats.agent.remarketing.activities.watchdog_activities import (
            _is_quiet_hours_for_session,
        )

        # 04:00 UTC = 23:00 Colombia. Fuera del rango 08-22.
        utc_11pm_co = datetime(2026, 6, 2, 4, 0, 0, tzinfo=ZoneInfo("UTC"))
        assert _is_quiet_hours_for_session("wa_+573001112233", utc_11pm_co) is True

    def test_quiet_hours_respect_env_var_override(self, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from src.plugins.chats.agent.remarketing.activities.watchdog_activities import (
            _is_quiet_hours_for_session,
        )

        # Operador setea 09-21 (más estricto). 08:30 ya es quiet.
        monkeypatch.setenv("WATCHDOG_QUIET_HOURS_START", "9")
        monkeypatch.setenv("WATCHDOG_QUIET_HOURS_END", "21")
        # 13:30 UTC = 08:30 Colombia. Con start=9, es quiet.
        utc_830am_co = datetime(2026, 6, 1, 13, 30, 0, tzinfo=ZoneInfo("UTC"))
        assert _is_quiet_hours_for_session("wa_+573001112233", utc_830am_co) is True


@pytest.mark.asyncio
async def test_eligibility_returns_outside_quiet_hours_at_3am(
    monkeypatch: pytest.MonkeyPatch,
    _isolate_vault_dir: Path,
) -> None:
    """Integration: el activity respeta quiet hours.

    Hack monkeypatch del datetime.utcnow() para simular las 03:00 Colombia.
    """
    monkeypatch.setenv("WATCHDOG_ENABLED", "1")
    now_ms = int(time.time() * 1000)
    _write_metadata(_isolate_vault_dir, SESSION_ID, _base_metadata(now_ms=now_ms))

    # Monkeypatch del datetime.utcnow del módulo del activity
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import src.plugins.chats.agent.remarketing.activities.watchdog_activities as wa_mod

    fixed_utc = datetime(2026, 6, 1, 8, 0, 0)  # 03:00 Colombia

    class FixedDatetime:
        @staticmethod
        def utcnow():
            return fixed_utc

        @staticmethod
        def now(tz=None):
            if tz is not None:
                return datetime(2026, 6, 1, 8, 0, 0, tzinfo=ZoneInfo("UTC")).astimezone(tz)
            return fixed_utc

    monkeypatch.setattr(wa_mod, "datetime", FixedDatetime)

    result = await check_watchdog_eligibility_activity(SESSION_ID, EPISODE_ID)

    assert result.eligible is False
    assert result.reason == "outside_quiet_hours"
