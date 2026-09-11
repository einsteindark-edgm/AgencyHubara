"""Los tests del connector usan los teléfonos sintéticos permitidos por el
scanner; la lista cerrada de MBA (fail-closed en prod) los incluye acá."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _mba_test_customers_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.platform import config

    monkeypatch.setattr(
        config, "MBA_CUSTOMER_ALLOWLIST", frozenset({"573001234567", "573009876543"})
    )


# Valores por tenant que Terraform materializa en SSM y agent.yaml resuelve (`${VAR}`, D3.1).
TENANT_ENV = {
    "WHATSAPP_PHONE_NUMBER_ID": "1234091093112024",
    "HUBARA_PUBLIC_API_URL": "https://api.example.test",
    "META_FLOW_ID_SHIPPING": "951293630651590",
    "MBA_CUSTOMER_ALLOWLIST": "+573001234567,+573009876543",
}


@pytest.fixture
def tenant_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    for k, v in TENANT_ENV.items():
        monkeypatch.setenv(k, v)
    return dict(TENANT_ENV)
