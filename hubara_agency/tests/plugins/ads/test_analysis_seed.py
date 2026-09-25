"""Seed del análisis con IA por campaña — capa PURA (`src.plugins.ads.analysis_seed`).

Caso Halloween (2026-09-25): el análisis mezclaba toda la cuenta con las ventas de toda
la tienda (fechadas en UTC) y botaba los días sin ventas → retorno 3.05 cuando el real
era 1.43. Ahora, para UNA campaña:

- `manual_sales` = ventas CONFIRMADAS atribuidas a sus chats, por día de inicio del chat
  en hora de Bogotá, con los días sin venta EN CERO (nunca se excluyen).
- `campaign_breakdown` = el drill-down que consume `ctwa-scorecard` de GraphAgents:
  anuncio → segmento, métricas de Meta del periodo + serie diaria, y cada chat con su
  tarjeta (headline) y el estado + monto de su pedido.
"""
from __future__ import annotations

import datetime

from src.plugins.ads.aggregation import AdsAttributedConversation
from src.plugins.ads.analysis_seed import attributed_daily_sales, build_campaign_breakdown


def _ms(iso: str) -> int:
    return int(datetime.datetime.fromisoformat(iso).replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)


def _conv(started_utc: str, *, source_id: str = "AD_1", state: str = "ganado", status: str | None = "paid",
          value: int | None = 52900, headline: str = "Velas aromáticas") -> AdsAttributedConversation:
    return AdsAttributedConversation(
        id="wa_x__ep_001", phone_number="x", episode_id="ep_001", started_at_ms=_ms(started_utc),
        last_msg_at_ms=None, msgs_count=5, ad_headline=headline, agent="ventas", state=state,
        value=value if status == "paid" else None, source_id=source_id, order_status=status,
        order_value_cop=value if status else None,
    )


def _actions(conv: int) -> list[dict]:
    return [{"action_type": "link_click", "value": "10"},
            {"action_type": "onsite_conversion.messaging_conversation_started_7d", "value": str(conv)}]


def test_ventas_diarias_atribuidas_en_hora_de_bogota_y_dias_sin_venta_en_cero() -> None:
    convs = [
        _conv("2026-09-21T03:00:00"),                       # 22:00 del 20 en Bogotá → cuenta el 20
        _conv("2026-09-21T15:00:00", status="pending"),      # pendiente: no es venta confirmada
        _conv("2026-09-21T16:00:00", status="cancelled"),    # cancelada: no cuenta
        _conv("2026-09-19T15:00:00", status=None, state="calificado"),
    ]
    out = attributed_daily_sales(convs, dates=["2026-09-19", "2026-09-20", "2026-09-21"])
    assert out == {"sales": [
        {"date": "2026-09-19", "total_orders": 0, "total_revenue": 0},
        {"date": "2026-09-20", "total_orders": 1, "total_revenue": 52900},
        {"date": "2026-09-21", "total_orders": 0, "total_revenue": 0},
    ]}


def test_una_venta_fuera_de_los_dias_de_meta_igual_aparece() -> None:
    out = attributed_daily_sales([_conv("2026-09-22T15:00:00")], dates=["2026-09-21"])
    assert [s["date"] for s in out["sales"]] == ["2026-09-21", "2026-09-22"]


PERIOD = [
    {"ad_id": "AD_1", "ad_name": "Liliana / Presentación / Video / Decoración", "adset_id": "S1",
     "adset_name": "LILIANA / abierta / Conversiones", "campaign_id": "C1", "spend": "40145.00",
     "impressions": "3821", "reach": "3066", "inline_link_clicks": "79", "actions": _actions(19)},
    {"ad_id": "AD_2", "ad_name": "Liliana / Presentación / Imagen / Beneficios", "adset_id": "S2",
     "adset_name": "LILIANA / similar / Conversiones", "campaign_id": "C1", "spend": "665",
     "impressions": "40", "reach": "38", "inline_link_clicks": "0", "actions": []},
]
DAILY = [
    {"ad_id": "AD_1", "date_start": "2026-09-17", "spend": "20000.4", "impressions": "1900",
     "inline_link_clicks": "40", "actions": _actions(9)},
    {"ad_id": "AD_1", "date_start": "2026-09-16", "spend": "20144.6", "impressions": "1921",
     "inline_link_clicks": "39", "actions": _actions(10)},
]


def _breakdown(convs: list) -> dict:
    return build_campaign_breakdown(
        campaign_id="C1", campaign_name="Halloween", budget_level="campaign",
        since="2026-09-11", until="2026-09-25", period_rows=PERIOD, daily_rows=DAILY,
        conversations=convs, orders_stale=True)


def test_el_drill_down_que_consume_el_scorecard() -> None:
    convs = [
        _conv("2026-09-21T15:00:00", source_id="AD_1"),
        _conv("2026-09-23T15:00:00", source_id="AD_1", status="pending", value=45000),
        _conv("2026-09-24T15:00:00", source_id="AD_1", status=None, state="calificado", headline="Chatea con nosotros"),
    ]
    b = _breakdown(convs)
    assert b["schema"] == 1
    assert b["campaign"] == {"id": "C1", "name": "Halloween", "budget_level": "campaign"}
    assert b["window"] == {"since": "2026-09-11", "until": "2026-09-25", "timezone": "America/Bogota"}
    assert b["orders_stale"] is True
    ad1 = b["ads"][0]
    assert (ad1["ad_id"], ad1["adset_id"], ad1["adset_name"]) == ("AD_1", "S1", "LILIANA / abierta / Conversiones")
    assert ad1["meta"] == {"spend_cop": 40145, "impressions": 3821, "reach": 3066, "link_clicks": 79, "conversations": 19}
    # serie diaria ordenada por fecha, gasto redondeado a pesos:
    assert ad1["daily"] == [
        {"date": "2026-09-16", "spend_cop": 20145, "impressions": 1921, "link_clicks": 39, "conversations": 10},
        {"date": "2026-09-17", "spend_cop": 20000, "impressions": 1900, "link_clicks": 40, "conversations": 9},
    ]
    assert ad1["chats"] == [
        {"date": "2026-09-21", "headline": "Velas aromáticas", "state": "ganado",
         "order": {"status": "paid", "value_cop": 52900}},
        {"date": "2026-09-23", "headline": "Velas aromáticas", "state": "ganado",
         "order": {"status": "pending", "value_cop": 45000}},
        {"date": "2026-09-24", "headline": "Chatea con nosotros", "state": "calificado", "order": None},
    ]
    ad2 = b["ads"][1]
    assert ad2["meta"]["conversations"] == 0 and ad2["chats"] == [] and ad2["daily"] == []


def test_chats_de_un_anuncio_sin_datos_de_meta_no_se_pierden() -> None:
    # anuncio borrado / fuera del insight: sus chats siguen contando (con gasto 0 conocido).
    b = _breakdown([_conv("2026-09-21T15:00:00", source_id="AD_BORRADO")])
    extra = b["ads"][-1]
    assert extra["ad_id"] == "AD_BORRADO"
    assert extra["adset_name"] == "Sin segmento (sin datos de Meta)"
    assert extra["meta"]["spend_cop"] == 0
    assert len(extra["chats"]) == 1
