"""MedusaSettings — lectura de env vars."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.platform.medusa.settings import MedusaSettings


def test_loads_base_url_and_token(monkeypatch):
    monkeypatch.setenv("MEDUSA_BASE_URL", "https://m.test")
    monkeypatch.setenv("MEDUSA_ADMIN_TOKEN", "sk_abc")
    monkeypatch.delenv("MEDUSA_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("MEDUSA_ADMIN_PASSWORD", raising=False)
    s = MedusaSettings()
    assert s.base_url == "https://m.test"
    assert s.admin_token == "sk_abc"
    assert s.admin_email is None
    assert s.http_timeout == 30.0


def test_raises_when_base_url_missing(monkeypatch):
    monkeypatch.delenv("MEDUSA_BASE_URL", raising=False)
    monkeypatch.delenv("MEDUSA_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("MEDUSA_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("MEDUSA_ADMIN_PASSWORD", raising=False)
    with pytest.raises(ValidationError):
        MedusaSettings()


def test_raises_when_base_url_is_empty_string(monkeypatch):
    """Premortem web-cart FM-09: compose inyecta `MEDUSA_BASE_URL=""` cuando
    la var no está exportada en el host. Un string vacío debe fallar IGUAL
    que la var ausente — si validara, los consumers (orders composition)
    recibirían clientes con base_url="" que fallan por-request con
    UnsupportedProtocol en vez de degradar limpio en composición."""
    monkeypatch.setenv("MEDUSA_BASE_URL", "")
    with pytest.raises(ValidationError):
        MedusaSettings()
    monkeypatch.setenv("MEDUSA_BASE_URL", "   ")
    with pytest.raises(ValidationError):
        MedusaSettings()


def test_custom_timeout_via_env(monkeypatch):
    monkeypatch.setenv("MEDUSA_BASE_URL", "https://m.test")
    monkeypatch.setenv("MEDUSA_HTTP_TIMEOUT", "60.5")
    s = MedusaSettings()
    assert s.http_timeout == 60.5
