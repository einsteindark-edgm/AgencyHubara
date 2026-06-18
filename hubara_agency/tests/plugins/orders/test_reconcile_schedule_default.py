"""ACK-5 — el default del intervalo de reconcile sale del CATÁLOGO, no de un
constante duplicada en el worker.

`config/schedulers.yaml` (ACK-3) ya es el contrato de defaults. Acá probamos
que el worker_boot LO LEE (capa main/boot → leer config es R-DET-safe), de modo
que el default-en-código deja de poder driftear contra el default-en-catálogo.

Cada plugin lee el YAML de forma independiente con stdlib+yaml — NO un módulo
central compartido (rompería R-DIP / ratchet P-28).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from src.plugins.orders.workers import reconcile

_CATALOG_PATH = Path(reconcile.__file__).resolve().parents[4] / "config" / "schedulers.yaml"


def _catalog_default(scheduler_id: str) -> str:
    data = yaml.safe_load(_CATALOG_PATH.read_text(encoding="utf-8"))
    entry = next(e for e in data["schedulers"] if e["id"] == scheduler_id)
    return str(entry["default"])


def test_interval_minutes_reads_default_from_catalog(tmp_path, monkeypatch):
    """Con el env sin setear, el default sale del catálogo (no del constante)."""
    cat = tmp_path / "schedulers.yaml"
    cat.write_text(
        "version: 1\nschedulers:\n  - id: orders-reconcile\n    default: '9'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(reconcile, "_CATALOG_PATH", cat)
    monkeypatch.delenv("ORDER_RECONCILE_INTERVAL_MINUTES", raising=False)

    assert reconcile._interval_minutes() == 9


def test_env_still_overrides_catalog(tmp_path, monkeypatch):
    """El env sigue gobernando producción (precedencia env > catálogo)."""
    cat = tmp_path / "schedulers.yaml"
    cat.write_text(
        "version: 1\nschedulers:\n  - id: orders-reconcile\n    default: '9'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(reconcile, "_CATALOG_PATH", cat)
    monkeypatch.setenv("ORDER_RECONCILE_INTERVAL_MINUTES", "17")

    assert reconcile._interval_minutes() == 17


def test_fallback_constant_matches_catalog_default() -> None:
    """El fallback (catálogo ausente) no puede MENTIR sobre el default real."""
    assert str(reconcile._DEFAULT_INTERVAL_MIN) == _catalog_default("orders-reconcile")
