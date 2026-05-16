"""Boundary DTOs del catalog_sync (R-JSON).

`products_json: str` aplica el JSON-string trick (gotcha #6 del DEHA arch)
para transferir listas grandes a traves del workflow boundary sin tipos
complejos anidados.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogSyncInput:
    """Input del CatalogSyncWorkflow.

    `snapshot_dir`: resuelto por el caller (Schedule script) o por la
    activity de fallback. Cruzar como `str` cumple R-JSON. Si llega vacio,
    `write_snapshot_activity` lo resuelve desde env via
    `src/platform/catalog/paths.py:get_snapshot_dir()` (la activity es el
    unico legitimado para leer env — R-DET prohibe hacerlo desde
    `@workflow.run`).
    """

    tenant_id: str = "default"
    force_full_refresh: bool = True
    snapshot_dir: str = ""


@dataclass(frozen=True)
class PullCatalogResult:
    products_json: str
    count: int
    fetched_at: str  # ISO 8601 UTC
    source_etag: str | None = None


@dataclass(frozen=True)
class WriteSnapshotInput:
    products_json: str
    count: int
    fetched_at: str
    snapshot_dir: str
    source_etag: str | None = None


@dataclass(frozen=True)
class WriteSnapshotResult:
    version: str
    bytes_written: int
    files_written: int
