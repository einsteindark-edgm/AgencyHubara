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


@pytest.mark.asyncio
async def test_eligible_resolves_awaiting_quote_template(
    monkeypatch: pytest.MonkeyPatch,
    _isolate_vault_dir: Path,
) -> None:
    """tag=INTERESADO + no registered_order → awaiting_quote stage → resolves
    `quote_ready_utility_v1`."""
    monkeypatch.setenv("WATCHDOG_ENABLED", "1")
    now_ms = int(time.time() * 1000)
    md = _base_metadata(now_ms=now_ms)
    # window in 25min — within WATCHDOG_PRE_EXPIRY_MS (30min)
    md["service_window_expires_at_ms"] = now_ms + 25 * 60 * 1000
    _write_metadata(_isolate_vault_dir, SESSION_ID, md)

    result = await check_watchdog_eligibility_activity(SESSION_ID, EPISODE_ID)

    assert result.eligible is True
    assert result.reason is None
    assert result.resolved_template_name == "quote_ready_utility_v1"
    assert result.resolved_template_variables is not None
    # Variables should include the declared ones from the template spec.
    assert "customer_first_name" in result.resolved_template_variables
    assert "product_or_quote_label" in result.resolved_template_variables


@pytest.mark.asyncio
async def test_eligible_resolves_awaiting_payment_with_registered_order(
    monkeypatch: pytest.MonkeyPatch,
    _isolate_vault_dir: Path,
) -> None:
    """registered_order present + episode NOT closed COMPRA_EXITOSA →
    awaiting_payment stage → resolves `payment_pending_utility_v1`."""
    monkeypatch.setenv("WATCHDOG_ENABLED", "1")
    now_ms = int(time.time() * 1000)
    md = _base_metadata(now_ms=now_ms)
    md["service_window_expires_at_ms"] = now_ms + 20 * 60 * 1000
    md["registered_order"] = {"order_id": "ORD-1042", "success": True}
    _write_metadata(_isolate_vault_dir, SESSION_ID, md)

    result = await check_watchdog_eligibility_activity(SESSION_ID, EPISODE_ID)

    assert result.eligible is True
    assert result.resolved_template_name == "payment_pending_utility_v1"
    assert (
        result.resolved_template_variables is not None
        and result.resolved_template_variables.get("order_reference") == "ORD-1042"
    )


@pytest.mark.asyncio
async def test_pre_expiry_constant_is_30_minutes() -> None:
    """Sanity check on the constant used by the activity. If this changes,
    the eligibility window math above must move too."""
    assert WATCHDOG_PRE_EXPIRY_MS == 30 * 60 * 1000
