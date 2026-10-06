"""El agente y el dashboard dicen lo mismo sobre la edad de la copia local.

La copia del catálogo se refresca A MANO (botón Sync del dashboard, decisión
del operador 2026-10-06). El dashboard (`GET /api/catalog/snapshot`) la daba
por vieja con `>=` y el cliente del agente con `>`: justo a los 30 minutos
uno decía vieja y el otro no. Ahora es UNA regla: vieja desde que cumple
`CATALOG_MAX_AGE_MINUTES`, inclusive.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.plugins.catalog.api as catalog_api
from src.platform.catalog import local_snapshot
from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient

NOW = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)


class _Frozen(datetime):
    """El reloj de las dos lecturas, fijo en `NOW`."""

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001, ANN206 - firma de datetime.now
        return NOW if tz is None else NOW.astimezone(tz)


def _copy(snapshot_dir: Path, fetched_at: datetime | str) -> None:
    (snapshot_dir / "snapshot.json").write_text(
        json.dumps([{"id": "1", "handle": "h", "title": "T", "status": "published"}]),
        encoding="utf-8",
    )
    stamp = fetched_at.isoformat() if isinstance(fetched_at, datetime) else fetched_at
    (snapshot_dir / "manifest.json").write_text(
        json.dumps({"version": "v", "fetched_at": stamp, "product_count": 1}),
        encoding="utf-8",
    )


async def _both(snapshot_dir: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[bool, bool]:
    monkeypatch.setattr(local_snapshot, "datetime", _Frozen)
    monkeypatch.setattr(catalog_api, "datetime", _Frozen)
    monkeypatch.setenv("CATALOG_SNAPSHOT_DIR", str(snapshot_dir))
    monkeypatch.setenv("CATALOG_MAX_AGE_MINUTES", "30")
    agent = (await LocalSnapshotCatalogClient(snapshot_dir, max_age_minutes=30).search(q="h")).stale
    dashboard = (await catalog_api.get_snapshot())["stale"]
    return agent, dashboard


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("age", "old"),
    [
        (timedelta(minutes=30), True),
        (timedelta(minutes=29, seconds=59), False),
        (timedelta(minutes=31), True),
    ],
)
async def test_the_agent_and_the_dashboard_agree_on_when_the_copy_is_old(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, age: timedelta, old: bool
) -> None:
    _copy(tmp_path, NOW - age)

    assert await _both(tmp_path, monkeypatch) == (old, old)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fetched_at", "old"),
    [
        ("no-es-una-fecha", True),  # ilegible: vieja para los dos
        ("2026-10-06T14:30:00", True),  # sin zona = UTC: cumple los 30 minutos
        ("2026-10-06T14:31:00", False),  # sin zona = UTC: 29 minutos
        ("2026-10-06T14:30:00Z", True),
    ],
)
async def test_an_unreadable_or_zoneless_date_reads_the_same_for_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fetched_at: str, old: bool
) -> None:
    """Revisión del PR #394: con una fecha ilegible el agente decía vieja y
    el dashboard no; sin zona horaria el dashboard se caía (resta de una
    fecha con zona y otra sin ella)."""
    _copy(tmp_path, fetched_at)

    assert await _both(tmp_path, monkeypatch) == (old, old)


@pytest.mark.asyncio
async def test_a_manifest_without_its_date_is_old_for_both(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin `fetched_at` no se sabe la edad de la copia: vieja para los dos.
    (Antes el dashboard la daba por nueva; ahora rige la regla unificada.)"""
    _copy(tmp_path, NOW)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"version": "v", "product_count": 1}), encoding="utf-8")

    assert await _both(tmp_path, monkeypatch) == (True, True)
    assert (await catalog_api.get_snapshot())["age_minutes"] is None
