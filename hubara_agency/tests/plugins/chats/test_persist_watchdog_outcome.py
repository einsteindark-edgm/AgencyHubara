"""Tests for `persist_watchdog_outcome_activity` (HU-WA24H-001 Sprint 2).

Each test writes a starter metadata.json, invokes the activity with one of
the four outcomes, and asserts the resulting `metadata.watchdog` block. Uses
the `_isolate_vault_dir` autouse fixture from conftest.

Outcomes covered:
  * "fired"    → fired_at_ms set, cancelled_at_ms None
  * "cancelled" → cancelled_at_ms set, reason_cancelled = detail
  * "skipped"  → cancelled_at_ms set, reason_cancelled = "skipped:<detail>"
  * "failed"   → cancelled_at_ms set, reason_cancelled = "failed:<detail>"
  * invalid    → ValueError
  * idempotent → calling twice with same outcome doesn't corrupt other keys
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.plugins.chats.agent.remarketing.activities.watchdog_activities import (
    persist_watchdog_outcome_activity,
)


SESSION_ID = "wa_+57300persist"


def _read_metadata(vault_dir: Path, session_id: str) -> dict:
    path = vault_dir / session_id / "metadata.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_metadata(vault_dir: Path, session_id: str, data: dict) -> None:
    target = vault_dir / session_id / "metadata.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2), encoding="utf-8")


@pytest.mark.asyncio
async def test_fired_outcome_sets_fired_at_clears_cancelled(
    _isolate_vault_dir: Path,
) -> None:
    """Outcome 'fired' → fired_at_ms set, cancelled_at_ms and reason_cancelled
    explicitly None (clears any stale cancel state from a previous turn)."""
    _write_metadata(_isolate_vault_dir, SESSION_ID, {"phone_number_id": "PID"})

    await persist_watchdog_outcome_activity(SESSION_ID, "fired", "MOCK_WATCHDOG_abc")

    md = _read_metadata(_isolate_vault_dir, SESSION_ID)
    wd = md["watchdog"]
    assert isinstance(wd["fired_at_ms"], int)
    assert wd["cancelled_at_ms"] is None
    assert wd["reason_cancelled"] is None
    # Did NOT touch unrelated keys.
    assert md["phone_number_id"] == "PID"


@pytest.mark.asyncio
async def test_cancelled_outcome_persists_reason(
    _isolate_vault_dir: Path,
) -> None:
    _write_metadata(_isolate_vault_dir, SESSION_ID, {})

    await persist_watchdog_outcome_activity(
        SESSION_ID, "cancelled", "customer_replied"
    )

    md = _read_metadata(_isolate_vault_dir, SESSION_ID)
    wd = md["watchdog"]
    assert isinstance(wd["cancelled_at_ms"], int)
    assert wd["reason_cancelled"] == "customer_replied"


@pytest.mark.asyncio
async def test_skipped_outcome_prefixes_reason_with_skipped(
    _isolate_vault_dir: Path,
) -> None:
    _write_metadata(_isolate_vault_dir, SESSION_ID, {})

    await persist_watchdog_outcome_activity(SESSION_ID, "skipped", "feature_flag_off")

    md = _read_metadata(_isolate_vault_dir, SESSION_ID)
    wd = md["watchdog"]
    assert wd["reason_cancelled"] == "skipped:feature_flag_off"


@pytest.mark.asyncio
async def test_failed_outcome_prefixes_reason_with_failed(
    _isolate_vault_dir: Path,
) -> None:
    _write_metadata(_isolate_vault_dir, SESSION_ID, {})

    await persist_watchdog_outcome_activity(SESSION_ID, "failed", "Meta 500 timeout")

    md = _read_metadata(_isolate_vault_dir, SESSION_ID)
    wd = md["watchdog"]
    assert wd["reason_cancelled"] == "failed:Meta 500 timeout"


@pytest.mark.asyncio
async def test_invalid_outcome_raises(
    _isolate_vault_dir: Path,
) -> None:
    _write_metadata(_isolate_vault_dir, SESSION_ID, {})

    with pytest.raises(ValueError, match="Invalid watchdog outcome"):
        await persist_watchdog_outcome_activity(SESSION_ID, "exploded", None)


@pytest.mark.asyncio
async def test_outcome_persists_through_existing_watchdog_state(
    _isolate_vault_dir: Path,
) -> None:
    """Idempotent overwrite — calling `fired` after a prior `cancelled` should
    update the block coherently (latest-wins semantics)."""
    starter = {
        "watchdog": {
            "workflow_id": "watchdog-old",
            "scheduled_for_ms": 1000,
            "fired_at_ms": None,
            "cancelled_at_ms": 2000,
            "reason_cancelled": "stale",
        }
    }
    _write_metadata(_isolate_vault_dir, SESSION_ID, starter)

    await persist_watchdog_outcome_activity(SESSION_ID, "fired", "MOCK_X")

    wd = _read_metadata(_isolate_vault_dir, SESSION_ID)["watchdog"]
    assert isinstance(wd["fired_at_ms"], int)
    assert wd["cancelled_at_ms"] is None
    assert wd["reason_cancelled"] is None
    # workflow_id was NOT in the new payload — should be preserved from prior.
    assert wd["workflow_id"] == "watchdog-old"


@pytest.mark.asyncio
async def test_persist_outcome_writes_under_the_store_lock_and_only_touches_the_watchdog_block(
    monkeypatch: pytest.MonkeyPatch, _isolate_vault_dir,
) -> None:
    """Otro escritor (ingest, connector tools de MBA, el contrato /tag) puede
    escribir metadata.json entre la lectura y la escritura del outcome. Con
    read→write plano, ese cambio (p.ej. `active_route=humano` de una
    escalación) se perdía."""
    import json
    from pathlib import Path

    from src.platform.state import FilesystemMetadataStore
    from src.plugins.chats.agent.remarketing.activities import watchdog_activities

    session = "wa_573001234567"
    target = Path(_isolate_vault_dir) / session / "metadata.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"tag": "NO_ETIQUETADO", "active_route": "ventas"}), encoding="utf-8")

    class _RacingStore(FilesystemMetadataStore):
        def update(self, session_id, mutator):
            def _with_foreign_write(data):
                data["active_route"] = "humano"  # escalación concurrente, ya en disco cuando el lock se toma
                data["tag"] = "HUMANO"
                return mutator(data)

            return super().update(session_id, _with_foreign_write)

    monkeypatch.setattr(watchdog_activities, "FilesystemMetadataStore", _RacingStore)
    await watchdog_activities.persist_watchdog_outcome_activity(session, "skipped", "feature_flag_off")
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["active_route"] == "humano" and data["tag"] == "HUMANO"
    assert data["watchdog"]["reason_cancelled"] == "skipped:feature_flag_off"
    assert isinstance(data["watchdog"]["cancelled_at_ms"], int)
