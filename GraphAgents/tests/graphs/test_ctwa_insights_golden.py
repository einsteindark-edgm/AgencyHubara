"""Golden-replay de `ctwa-insights`: inyecta la tool meta-ads-insights, le da el JSON
de Graph /insights (fixture real-shaped) y asierta el colapso a nivel cuenta por día.

06-15: Duo(120000,80,40) + Día del padre(176294,344,120) = 296294, 424, 160
06-16: Duo(100000,50,30) + Día del padre(90000,60,0)    = 190000, 110, 30
(Día del padre es purchase-conversion: vía Graph /insights el actions SÍ trae la conv.)
"""
from __future__ import annotations

import json
from pathlib import Path

from graphs.ctwa_insights import run
from tools.meta_ads_insights.impl import run as meta_ads_insights

GA = Path(__file__).resolve().parents[2]
FIX = GA / "fixtures" / "meta_insights_campaigns.json"


def test_golden_colapsa_a_cuenta_por_dia() -> None:
    payload = json.loads(FIX.read_text(encoding="utf-8"))
    out = run({"payload": payload}, tools={"meta-ads-insights": meta_ads_insights})
    assert out["currency"] == "COP"
    assert out["insights"] == [
        {"date": "2026-06-15", "spend_cop": 296294, "inline_link_clicks": 424, "conversations_started": 160},
        {"date": "2026-06-16", "spend_cop": 190000, "inline_link_clicks": 110, "conversations_started": 30},
    ]


def test_falta_payload_da_error_de_dominio() -> None:
    import pytest
    with pytest.raises(ValueError):  # MF-7: no KeyError crudo
        run({}, tools={"meta-ads-insights": meta_ads_insights})
