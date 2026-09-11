"""`default_deps()`: una factory sin config degrada a None, no tumba el proceso."""

from __future__ import annotations

import pytest

from src.plugins.mba.tools import deps as mod


def test_factory_failure_degrades_to_none_and_others_survive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom():
        raise RuntimeError("MEDUSA_BASE_URL ausente")

    monkeypatch.setattr(mod, "get_checkout_verification_port", boom)
    monkeypatch.setattr(mod, "get_catalog_client", lambda: "catalog")
    monkeypatch.setattr(mod, "get_order_query_port", lambda: "orders")
    mod.default_deps.cache_clear()
    try:
        d = mod.default_deps()
        assert (
            d.checkout is None and d.catalog == "catalog" and d.order_query == "orders"
        )
        assert mod.default_deps() is d
    finally:
        mod.default_deps.cache_clear()
