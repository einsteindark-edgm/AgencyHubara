"""Per-tool TCK de `parse-manual-sales` — golden de la ingesta de ventas (portado de
test_ingest.py): alias total_revenue -> cop, validación fuerte (fecha mala / falta revenue = error)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sdk.testkit.tool_checks import run_tool_checks, tool_level
from sdk.tool_model import load_tool
from tools.parse_manual_sales.impl import run

GA = Path(__file__).resolve().parents[2]
TOOL = GA / "tools" / "parse_manual_sales" / "tool.yaml"


def test_contrato_carga_y_certifica_C2() -> None:
    c = load_tool(TOOL)
    assert run_tool_checks(c, GA)["errors"] == []
    assert tool_level(c, GA) == "C2"


def test_golden_normaliza_y_mapea_alias() -> None:
    out = run(payload={"sales": [
        {"date": "2026-06-01", "total_orders": 15, "total_revenue": 450000},   # alias -> cop
        {"date": "2026-06-02", "total_orders": 8, "total_revenue_cop": 240000},
    ]})
    assert out == {"sales": [
        {"date": "2026-06-01", "total_orders": 15, "total_revenue_cop": 450000},
        {"date": "2026-06-02", "total_orders": 8, "total_revenue_cop": 240000},
    ]}


def test_acepta_lista_y_json_string() -> None:
    rows = [{"date": "2026-06-01", "total_orders": 1, "total_revenue": 1000}]
    expected = [{"date": "2026-06-01", "total_orders": 1, "total_revenue_cop": 1000}]
    assert run(payload=rows)["sales"] == expected
    assert run(payload=json.dumps({"sales": rows}))["sales"] == expected


def test_fecha_mala_explota() -> None:
    with pytest.raises(ValueError):
        run(payload=[{"date": "not-a-date", "total_orders": 1, "total_revenue": 1}])


def test_falta_revenue_explota() -> None:
    with pytest.raises(ValueError):
        run(payload=[{"date": "2026-06-01", "total_orders": 1}])
