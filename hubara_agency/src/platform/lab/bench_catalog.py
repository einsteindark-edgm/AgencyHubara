"""Catálogo del banco para calificar una corrida (plan §5, PR 13).

El scorecard arma su contexto (aromas, colores, precios del catálogo) con un
`CatalogPort`. En la caja, ese catálogo es el snapshot que se exportó con el
banco (`bench/<banco>/catalog/`), que tiene horas o días: el tope de edad es
alto para que nunca se lea como "viejo" (en producción no lo estaba).
"""
from __future__ import annotations

from pathlib import Path

from src.platform.catalog.local_snapshot import LocalSnapshotCatalogClient
from src.platform.catalog.port import CatalogPort

#: Diez años: el snapshot del banco nunca es "viejo" para el laboratorio.
NEVER_STALE_MINUTES = 60 * 24 * 365 * 10


def bench_catalog_client(snapshot_dir: Path) -> CatalogPort:
    return LocalSnapshotCatalogClient(snapshot_dir=Path(snapshot_dir), max_age_minutes=NEVER_STALE_MINUTES)
