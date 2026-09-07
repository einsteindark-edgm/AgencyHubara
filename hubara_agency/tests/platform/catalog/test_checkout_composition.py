"""`get_checkout_verification_port()`: el verificador live compuesto por el SDK
(antes solo el worker de sales lo armaba a mano desde módulos vendor)."""
from __future__ import annotations

import pytest

from src.platform.catalog import composition
from src.platform.catalog.checkout_port import CheckoutVerificationPort
from src.platform.medusa import composition as medusa_composition


def test_factory_returns_a_singleton_checkout_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEDUSA_BASE_URL", "http://medusa.invalid")
    monkeypatch.setenv("MEDUSA_ADMIN_TOKEN", "dummy")
    for fn in (
        medusa_composition.get_medusa_settings, medusa_composition.get_medusa_client,
        medusa_composition.get_medusa_product_service, composition.get_checkout_verification_port,
    ):
        fn.cache_clear()
    try:
        port = composition.get_checkout_verification_port()
        assert isinstance(port, CheckoutVerificationPort)
        assert composition.get_checkout_verification_port() is port
    finally:
        composition.get_checkout_verification_port.cache_clear()
        medusa_composition.get_medusa_settings.cache_clear()
        medusa_composition.get_medusa_client.cache_clear()
        medusa_composition.get_medusa_product_service.cache_clear()
