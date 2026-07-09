"""Settings de Meta OAuth — lectura del entorno (META_OAUTH_CONFIG_ID opcional).

Las apps business-type de Meta autorizan vía Facebook Login for Business con una
"configuración" (config_id). Si el operador la setea en SSM/env, el login debe
usarla; si no, el flujo scope-based clásico sigue intacto.
"""
from __future__ import annotations

from src.plugins.ads.meta.settings import meta_settings


def test_config_id_defaults_to_none(monkeypatch) -> None:
    monkeypatch.delenv("META_OAUTH_CONFIG_ID", raising=False)
    assert meta_settings().config_id is None


def test_config_id_read_from_env(monkeypatch) -> None:
    monkeypatch.setenv("META_OAUTH_CONFIG_ID", "777888999")
    assert meta_settings().config_id == "777888999"
