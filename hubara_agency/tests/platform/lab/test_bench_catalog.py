"""Catálogo del banco para calificar una corrida (plan §5, PR 13): el
scorecard arma su contexto (aromas, colores, precios) desde el snapshot que
se exportó con el banco. Ese snapshot tiene horas o días: nunca "viejo"."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.sdk.labkit import bench_catalog_client


@pytest.mark.asyncio
async def test_the_bench_snapshot_is_read_and_never_stale(tmp_path: Path) -> None:
    product = {"id": "1", "handle": "corona", "title": "Corona de Redención", "status": "published",
               "variants": [{"id": "v1", "title": "Unico", "prices": [{"amount": "49000", "currency_code": "cop"}]}]}
    (tmp_path / "snapshot.json").write_text(json.dumps([product]), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"version": "v1", "fetched_at": "2026-09-10T00:00:00+00:00", "product_count": 1}), encoding="utf-8"
    )

    result = await bench_catalog_client(tmp_path).search(q="", limit=10)

    assert [p.title for p in result.results] == ["Corona de Redención"]
    assert result.stale is False
