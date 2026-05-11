"""Resolucion del snapshot directory + max age.

Unico lugar en `platform/catalog/` que lee `os.environ`. Sigue el patron
de `src/platform/config.py:9-25`.
"""
from __future__ import annotations

import os
from pathlib import Path

# Default = <repo>/hubara_agency/catalog_workspace/. Override en prod via env.
# Path(__file__).resolve() -> .../hubara_agency/src/platform/catalog/paths.py
# .parents[3] -> .../hubara_agency/
_DEFAULT_SNAPSHOT_DIR = (
    Path(__file__).resolve().parents[3] / "catalog_workspace"
).resolve()


def get_snapshot_dir() -> Path:
    raw = os.environ.get("CATALOG_SNAPSHOT_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    return _DEFAULT_SNAPSHOT_DIR


def get_max_age_minutes() -> int:
    raw = os.environ.get("CATALOG_MAX_AGE_MINUTES", "30")
    try:
        return int(raw)
    except ValueError:
        return 30
