"""Sin Medusa configurado, las líneas del reporte por producto responden vacío
con el motivo, como la lista (tarjeta del ciclo #49)."""
from __future__ import annotations

import pytest

from src.platform.orders.empty_query import EmptyOrderQuery
from src.platform.orders.query_port import OrderLinesQueryPort


@pytest.mark.asyncio
async def test_empty_query_lines_answer_empty_with_the_reason():
    port = EmptyOrderQuery()

    result = await port.list_lines(limit=100, offset=0)

    assert isinstance(port, OrderLinesQueryPort)
    assert result.orders == []
    assert result.catalog_available is False
    assert "MEDUSA_BASE_URL" in (result.error_detail or "")
    assert result.limit == 100
