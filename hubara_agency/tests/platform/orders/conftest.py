"""Fixtures compartidas de tests/platform/orders.

El cache display_id→backend_id (L-2) es estado process-wide: sin limpieza
entre tests, un `list()` de un test poblaría el cache y cambiaría qué
llamadas HTTP hace el siguiente (respx call-counts no deterministas).
"""
from __future__ import annotations

import pytest

from src.platform.orders import display_id_cache


@pytest.fixture(autouse=True)
def _clear_display_id_cache():
    display_id_cache.clear()
    yield
    display_id_cache.clear()
