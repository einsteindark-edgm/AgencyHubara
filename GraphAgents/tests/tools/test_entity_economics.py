"""Per-tool TCK de `entity-economics` — las métricas de UNA entidad (campaña, segmento,
anuncio o tarjeta) desde sus totales crudos. La usan el scorecard (calcula) y numbers-qa
(recomputa y reconcilia byte-for-byte) → vive UNA vez en el catálogo.

Regla: aritmética EXACTA (Decimal serializado); denominador 0 o dato ausente → None,
JAMÁS un número adivinado. Las ventas son las CONFIRMADAS (pagadas): pendientes y
canceladas no entran acá — el scorecard las lleva aparte."""
from __future__ import annotations

from pathlib import Path

from sdk.testkit.tool_checks import run_tool_checks, tool_level
from sdk.tool_model import load_tool
from tools.entity_economics.impl import run

GA = Path(__file__).resolve().parents[2]
TOOL = GA / "tools" / "entity_economics" / "tool.yaml"

TOTALS = {
    "spend_cop": 100000, "impressions": 5000, "reach": 4000, "link_clicks": 100,
    "conversations": 40, "chats": 20, "paid_sales": 4, "paid_revenue_cop": 250000,
}


def test_contrato_carga_y_certifica_C2() -> None:
    c = load_tool(TOOL)
    assert run_tool_checks(c, GA)["errors"] == []
    assert tool_level(c, GA) == "C2"


def test_impl_golden() -> None:
    assert run(payload=TOTALS) == {
        "roas": "2.5",                   # ventas confirmadas / gasto
        "cost_per_sale_cop": "25000",    # gasto / ventas confirmadas
        "cost_per_chat_cop": "5000",     # gasto / chats que llegaron al WhatsApp
        "chat_to_sale": "0.2",           # ventas / chats
        "frequency": "1.25",             # impresiones / alcance (desgaste)
        "ctr": "0.02",                   # clics al enlace / impresiones
        "click_to_chat": "0.4",          # conversaciones Meta / clics al enlace
        "avg_ticket_cop": "62500",       # ingreso / ventas
        "ad_share_of_revenue": "0.4",    # gasto / ingreso: de cada $100 vendidos, $40 son anuncios
    }


def test_denominador_cero_o_alcance_ausente_es_null_nunca_inventado() -> None:
    out = run(payload={**TOTALS, "reach": None, "paid_sales": 0, "paid_revenue_cop": 0,
                       "chats": 0, "link_clicks": 0, "impressions": 0})
    assert out["frequency"] is None          # sin alcance (agregado de varios anuncios) → no se estima
    assert out["cost_per_sale_cop"] is None  # 0 ventas → no hay costo por venta
    assert out["avg_ticket_cop"] is None
    assert out["ad_share_of_revenue"] is None
    assert out["cost_per_chat_cop"] is None
    assert out["chat_to_sale"] is None
    assert out["ctr"] is None
    assert out["click_to_chat"] is None
    assert out["roas"] == "0"                # gasto real sin ventas: retorno 0 REAL, no null


def test_sin_gasto_el_retorno_es_null() -> None:
    assert run(payload={**TOTALS, "spend_cop": 0})["roas"] is None


def test_idempotente() -> None:
    assert run(payload=TOTALS) == run(payload=TOTALS)
