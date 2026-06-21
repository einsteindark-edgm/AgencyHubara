"""Per-tool TCK de `diagnose` — golden de la tabla de decisión (portada de
test_diagnosis.py del motor). Umbrales: drop-off > 0.40 = fricción; MER < 2.0 = pobre.
Los bordes son INCLUSIVOS (0.40 no es > 0.40; 2.0 no es < 2.0 → saludable)."""
from __future__ import annotations

from pathlib import Path

import pytest

from sdk.testkit.tool_checks import run_tool_checks, tool_level
from sdk.tool_model import load_tool
from tools.diagnose.impl import run

GA = Path(__file__).resolve().parents[2]
TOOL = GA / "tools" / "diagnose" / "tool.yaml"


def test_contrato_carga_y_certifica_C2() -> None:
    c = load_tool(TOOL)
    assert run_tool_checks(c, GA)["errors"] == []
    assert tool_level(c, GA) == "C2"


@pytest.mark.parametrize("mer,drop,expected", [
    ("3.0", "0.2", {"high_friction": False, "poor_profitability": False, "recommendation": "scale_budget"}),
    ("1.5", "0.8", {"high_friction": True, "poor_profitability": True, "recommendation": "rotate_creative"}),
    ("1.0", "0.1", {"high_friction": False, "poor_profitability": True, "recommendation": "review_targeting_or_pricing"}),
    ("3.0", "0.7", {"high_friction": True, "poor_profitability": False, "recommendation": "scale_budget"}),
])
def test_golden_tabla_de_decision(mer, drop, expected) -> None:
    assert run(payload={"mer": mer, "drop_off_rate": drop}) == expected


def test_insufficient_data_cuando_falta_una_metrica() -> None:
    assert run(payload={"mer": None, "drop_off_rate": None}) == {
        "high_friction": False, "poor_profitability": False, "recommendation": "insufficient_data",
    }
    assert run(payload={"mer": "3.0", "drop_off_rate": None})["recommendation"] == "insufficient_data"


def test_bordes_inclusivos_son_saludables() -> None:
    # drop-off exactamente 0.40 NO es > 0.40; MER exactamente 2.0 NO es < 2.0 → saludable.
    assert run(payload={"mer": "2.0", "drop_off_rate": "0.40"}) == {
        "high_friction": False, "poor_profitability": False, "recommendation": "scale_budget",
    }


def test_revenue_cero_no_es_rotate_sino_no_revenue_recorded() -> None:
    # MF-6: mer == 0 (revenue 0) con fricción → NO "rotate_creative" (no culpes al creativo);
    # la venta CTWA puede no estar cargada/atribuida → señal distinta para verificar completitud.
    out = run(payload={"mer": "0", "drop_off_rate": "0.7"})
    assert out["recommendation"] == "no_revenue_recorded"
    assert out["poor_profitability"] is True
