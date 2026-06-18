"""ACK-5 — los defaults de cron del worker de evals salen del CATÁLOGO.

`config/schedulers.yaml` (ACK-3) es el contrato de defaults para
`sales-eval-online` y `golden-eval`. El worker_boot (main) lo LEE en vez de
duplicar el cron en un constante — leer config en main es R-DET-safe.

El plugin `chats` lee el YAML de forma independiente (stdlib+yaml), sin un
módulo central compartido (R-DIP / ratchet P-28).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from src.plugins.chats.workers import sales_eval

_CATALOG_PATH = Path(sales_eval.__file__).resolve().parents[4] / "config" / "schedulers.yaml"


def _catalog_default(scheduler_id: str) -> str:
    data = yaml.safe_load(_CATALOG_PATH.read_text(encoding="utf-8"))
    entry = next(e for e in data["schedulers"] if e["id"] == scheduler_id)
    return str(entry["default"])


def _write_catalog(tmp_path, online: str, golden: str) -> Path:
    cat = tmp_path / "schedulers.yaml"
    cat.write_text(
        "version: 1\n"
        "schedulers:\n"
        f"  - id: sales-eval-online\n    default: '{online}'\n"
        f"  - id: golden-eval\n    default: '{golden}'\n",
        encoding="utf-8",
    )
    return cat


def test_online_cron_reads_default_from_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(sales_eval, "_CATALOG_PATH", _write_catalog(tmp_path, "0 7 * * *", "0 9 * * *"))
    monkeypatch.delenv("SALES_EVAL_SCHEDULE_CRON", raising=False)
    assert sales_eval._online_cron() == "0 7 * * *"


def test_golden_cron_reads_default_from_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(sales_eval, "_CATALOG_PATH", _write_catalog(tmp_path, "0 7 * * *", "0 9 * * *"))
    monkeypatch.delenv("GOLDEN_EVAL_SCHEDULE_CRON", raising=False)
    assert sales_eval._golden_cron() == "0 9 * * *"


def test_env_still_overrides_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(sales_eval, "_CATALOG_PATH", _write_catalog(tmp_path, "0 7 * * *", "0 9 * * *"))
    monkeypatch.setenv("SALES_EVAL_SCHEDULE_CRON", "30 1 * * *")
    monkeypatch.setenv("GOLDEN_EVAL_SCHEDULE_CRON", "45 2 * * *")
    assert sales_eval._online_cron() == "30 1 * * *"
    assert sales_eval._golden_cron() == "45 2 * * *"


def test_fallback_constants_match_catalog_defaults() -> None:
    """Los fallbacks (catálogo ausente) no pueden mentir sobre el default real."""
    assert sales_eval._DEFAULT_CRON == _catalog_default("sales-eval-online")
    assert sales_eval._GOLDEN_DEFAULT_CRON == _catalog_default("golden-eval")
