"""paths.py — CATALOG_SNAPSHOT_DIR + CATALOG_MAX_AGE_MINUTES."""
from __future__ import annotations

from pathlib import Path

from src.platform.catalog.paths import get_max_age_minutes, get_snapshot_dir


def test_default_dir_when_no_env(monkeypatch):
    monkeypatch.delenv("CATALOG_SNAPSHOT_DIR", raising=False)
    p = get_snapshot_dir()
    assert isinstance(p, Path)
    assert p.is_absolute()


def test_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CATALOG_SNAPSHOT_DIR", str(tmp_path))
    assert get_snapshot_dir() == tmp_path.resolve()


def test_max_age_default(monkeypatch):
    monkeypatch.delenv("CATALOG_MAX_AGE_MINUTES", raising=False)
    assert get_max_age_minutes() == 30


def test_max_age_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("CATALOG_MAX_AGE_MINUTES", "abc")
    assert get_max_age_minutes() == 30


def test_max_age_int_override(monkeypatch):
    monkeypatch.setenv("CATALOG_MAX_AGE_MINUTES", "60")
    assert get_max_age_minutes() == 60
