"""Integración end-to-end del pod `ads-analytics`: inyecta el JSON de Meta (Graph
/insights) + las ventas, encadena las 5 capabilities (como el supervisor sequential) y
asierta el informe final. El feature completo corriendo en chico, determinista — sin
red, sin LLM, sin runtime durable (eso es G1+); prueba la COMPOSICIÓN.
"""
from __future__ import annotations

import json
from pathlib import Path

from graphs import blended_economics, ctwa_insights, ctwa_report, numbers_qa, sales_ledger
from tools.blended_unit_economics.impl import run as blended_unit_economics
from tools.diagnose.impl import run as diagnose
from tools.merge_by_date.impl import run as merge_by_date
from tools.meta_ads_insights.impl import run as meta_ads_insights
from tools.parse_manual_sales.impl import run as parse_manual_sales

GA = Path(__file__).resolve().parents[2]
META_FIX = GA / "fixtures" / "meta_insights_campaigns.json"

SALES = {"sales": [
    {"date": "2026-06-15", "total_orders": 12, "total_revenue": 600000},
    {"date": "2026-06-16", "total_orders": 4, "total_revenue": 150000},
]}


def test_pipeline_end_to_end() -> None:
    meta_payload = json.loads(META_FIX.read_text(encoding="utf-8"))

    # 1) extractores (en paralelo en el supervisor; acá secuencial)
    insights = ctwa_insights.run({"payload": meta_payload}, tools={"meta-ads-insights": meta_ads_insights})
    sales = sales_ledger.run({"payload": SALES}, tools={"parse-manual-sales": parse_manual_sales})

    # 2) analyzer (el corazón)
    blended = blended_economics.run(
        {"currency": insights["currency"], "insights": insights["insights"], "sales": sales["sales"]},
        tools={"merge-by-date": merge_by_date,
               "blended-unit-economics": blended_unit_economics,
               "diagnose": diagnose},
    )

    # 3) QA no-self-review + 4) reporter
    qa = numbers_qa.run(blended, tools={"blended-unit-economics": blended_unit_economics})
    report = ctwa_report.run(blended)

    # QA pasa: los números reconcilian (nadie los editó a mano).
    assert qa["passed"] is True
    # 06-15 y 06-16 matchean (insight + venta) → 2 días en el blend, 0 sin match.
    assert [d["date"] for d in blended["days"]] == ["2026-06-15", "2026-06-16"]
    assert blended["unmatched"] == {"meta_only": [], "sales_only": []}
    # "Día del padre" (purchase-conversion) aportó su conversación vía Graph /insights:
    # 06-15 conv = Duo 40 + Padre 120 = 160 (lo que el MCP ads_get_ad_entities no daba).
    assert blended["days"][0]["conversations_started"] == 160
    # el reporte sale con un verdict del periodo + la tabla determinista.
    assert report["verdict"] in (
        "scale_budget", "rotate_creative", "review_targeting_or_pricing",
        "no_revenue_recorded", "insufficient_data",
    )
    assert "Hubara — Ads Analytics (CTWA)" in report["markdown"]
    assert "2026-06-15" in report["markdown"]
