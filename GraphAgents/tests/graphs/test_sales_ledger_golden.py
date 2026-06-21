"""Golden-replay de `sales-ledger`: inyecta parse-manual-sales y asierta la normalización
(alias total_revenue -> total_revenue_cop)."""
from __future__ import annotations

from graphs.sales_ledger import run
from tools.parse_manual_sales.impl import run as parse_manual_sales


def test_golden_normaliza_ventas() -> None:
    payload = {"sales": [
        {"date": "2026-06-15", "total_orders": 15, "total_revenue": 450000},
        {"date": "2026-06-16", "total_orders": 8, "total_revenue_cop": 240000},
    ]}
    out = run({"payload": payload}, tools={"parse-manual-sales": parse_manual_sales})
    assert out == {"sales": [
        {"date": "2026-06-15", "total_orders": 15, "total_revenue_cop": 450000},
        {"date": "2026-06-16", "total_orders": 8, "total_revenue_cop": 240000},
    ]}


def test_falta_payload_da_error_de_dominio() -> None:
    import pytest
    with pytest.raises(ValueError):  # MF-7: no KeyError crudo
        run({}, tools={"parse-manual-sales": parse_manual_sales})
