"""Guard del catálogo centralizado de schedulers (ACK-3).

`config/schedulers.yaml` es la fuente única de verdad de TODOS los schedulers
que ACTIVAN funcionalidad (remarketing watchdog, evaluadores, reconcile, etc.).
Este test lo vuelve load-bearing: si el catálogo se desincroniza del código
vivo (un default de cadencia cambia y nadie actualiza el catálogo), CI rompe.

Espíritu: la regla de oro del plugin protocol — "ningún campo sin su check".
Acá: ningún scheduler sin su entrada en el catálogo, y ninguna entrada que
mienta sobre el default real del código.
"""
from __future__ import annotations

from pathlib import Path

import yaml

# Coupling con el código vivo: si estos cambian, el catálogo DEBE reflejarlo.
from src.platform.whatsapp.window import (
    SERVICE_WINDOW_MS,
    WATCHDOG_PRE_EXPIRY_MS,
)

_CATALOG_PATH = Path(__file__).resolve().parents[1] / "config" / "schedulers.yaml"

_REQUIRED_KEYS = {"id", "activates", "plugin", "cadence_kind", "source", "layer", "code_ref"}

# Todo scheduler que activa funcionalidad debe estar catalogado.
_EXPECTED_IDS = {
    "orders-reconcile",
    "sales-eval-online",
    "golden-eval",
    "remarketing-watchdog",
    "evaluate-episode",
    "catalog-sync",
}


def _load_catalog() -> dict:
    assert _CATALOG_PATH.exists(), f"Catálogo de schedulers ausente: {_CATALOG_PATH}"
    data = yaml.safe_load(_CATALOG_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "El catálogo debe ser un mapping de nivel raíz"
    return data


def test_catalog_has_version_and_scheduler_list() -> None:
    data = _load_catalog()
    assert data.get("version") == 1
    schedulers = data.get("schedulers")
    assert isinstance(schedulers, list) and schedulers, "schedulers debe ser una lista no vacía"


def test_every_scheduler_entry_is_well_formed() -> None:
    schedulers = _load_catalog()["schedulers"]
    for entry in schedulers:
        missing = _REQUIRED_KEYS - set(entry)
        assert not missing, f"Entrada {entry.get('id')!r} sin claves requeridas: {missing}"


def test_all_known_schedulers_are_catalogued() -> None:
    ids = {entry["id"] for entry in _load_catalog()["schedulers"]}
    missing = _EXPECTED_IDS - ids
    assert not missing, f"Schedulers que activan funcionalidad sin catalogar: {missing}"


def test_watchdog_window_defaults_match_live_code() -> None:
    """El catálogo no puede mentir sobre los defaults hardcoded del watchdog."""
    by_id = {e["id"]: e for e in _load_catalog()["schedulers"]}
    watchdog = by_id["remarketing-watchdog"]
    assert watchdog["pre_expiry_ms_default"] == WATCHDOG_PRE_EXPIRY_MS
    assert watchdog["service_window_ms"] == SERVICE_WINDOW_MS
