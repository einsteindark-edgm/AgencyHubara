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


def _copy(snapshot_dir: Path, fetched_at: datetime) -> None:
    (snapshot_dir / "snapshot.json").write_text(
        json.dumps([{"id": "1", "handle": "h", "title": "T", "status": "published"}]),
        encoding="utf-8",
    )
    (snapshot_dir / "manifest.json").write_text(
        json.dumps({"version": "v", "fetched_at": fetched_at.isoformat(), "product_count": 1}),
        encoding="utf-8",
    )


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
    monkeypatch.setattr(local_snapshot, "datetime", _Frozen)
    monkeypatch.setattr(catalog_api, "datetime", _Frozen)
    monkeypatch.setenv("CATALOG_SNAPSHOT_DIR", str(tmp_path))
    monkeypatch.setenv("CATALOG_MAX_AGE_MINUTES", "30")

    agent = (await LocalSnapshotCatalogClient(tmp_path, max_age_minutes=30).search(q="h")).stale
    dashboard = (await catalog_api.get_snapshot())["stale"]

    assert (agent, dashboard) == (old, old)
