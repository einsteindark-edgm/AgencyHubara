"""Los tests del connector usan los teléfonos sintéticos permitidos por el
scanner; la lista cerrada de MBA (fail-closed en prod) los incluye acá."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _mba_test_customers_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.platform import config

    monkeypatch.setattr(config, "MBA_CUSTOMER_ALLOWLIST", frozenset({"573001234567", "573009876543"}))
