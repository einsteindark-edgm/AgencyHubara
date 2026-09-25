"""Golden-replay de `ctwa-scorecard` sobre el caso REAL Halloween (2026-09-11..25).

El fixture `halloween_breakdown.json` es el drill-down del plugin `ads` en prod (sin
teléfonos): 4 segmentos × 3 piezas, 44 chats con su tarjeta (headline) y el estado del
pedido. Los esperados se derivaron A MANO del fixture (conteos + Decimal), no corriendo
el código — el golden es la especificación de las decisiones:

- Solo las ventas CONFIRMADAS (pagadas) cuentan para el retorno; pendientes y sin verificar
  van aparte como "por confirmar"; el cancelado se excluye aunque el chat diga «ganado».
- La campaña es CBO → nunca "subir un segmento".
- Pocos chats (<10) → "vigilar" con el umbral de pausa explícito, no una decisión.
- La tarjeta del carrusel que no vende se detecta por el headline de cada chat.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from graphs.ctwa_scorecard import run
from tools.entity_economics.impl import run as entity_economics

GA = Path(__file__).resolve().parents[2]
BREAKDOWN = json.loads((GA / "fixtures" / "halloween_breakdown.json").read_text(encoding="utf-8"))

AD_ABIERTA_VIDEO = "120247421007140317"
AD_INTERESES_VIDEO = "120247398115620317"
AD_SIMILAR_VIDEO = "120247421040590317"
AD_PERSO_CARRUSEL = "120247418828490317"
AD_PERSO_IMAGEN = "120247418828570317"


def _score() -> dict:
    return run({"breakdown": BREAKDOWN}, tools={"entity-economics": entity_economics})["scorecard"]


def _q(num: int, den: int) -> str:
    return str(Decimal(num) / Decimal(den))


def test_sin_breakdown_no_hay_scorecard() -> None:
    # análisis de cuenta completa (seed viejo): el pod sigue con el blend diario, sin drill-down.
    assert run({"breakdown": None}, tools={"entity-economics": entity_economics}) == {"scorecard": None}


def test_campana_solo_cuenta_ventas_confirmadas() -> None:
    c = _score()["campaign"]
    t = c["totals"]
    assert (t["spend_cop"], t["chats"]) == (148446, 44)
    assert (t["paid_sales"], t["paid_revenue_cop"]) == (4, 211600)
    assert (t["pending_sales"], t["pending_revenue_cop"]) == (1, 45000)
    assert (t["unverified_sales"], t["unverified_revenue_cop"]) == (1, 61940)
    # el cancelado se excluye aunque el chat siga diciendo «ganado»:
    assert (t["cancelled_sales"], t["cancelled_revenue_cop"]) == (1, 49500)
    assert (t["open_chats"], t["stale_open_chats"]) == (23, 14)
    assert c["metrics"]["roas"] == _q(211600, 148446)          # 1.43 confirmado
    assert c["potential_roas"] == _q(318540, 148446)            # 2.15 si se confirma lo pendiente
    assert c["budget_level"] == "campaign"


def test_veredicto_no_escalar_con_lo_confirmado() -> None:
    # 1.43 confirmado < 2 (punto de equilibrio); 2.15 solo si se confirman 2 pedidos.
    assert _score()["campaign"]["verdict"] == {"action": "hold_budget", "reason": "pending_confirmation"}


def test_segmentos_en_orden_de_gasto_con_su_decision() -> None:
    segs = _score()["segments"]
    assert [(s["label"], s["totals"]["spend_cop"], s["decision"]["action"]) for s in segs] == [
        ("Personalizada", 66972, "keep_pending"),
        ("Abierta", 40509, "keep_pending"),
        ("Intereses", 24160, "watch"),
        ("Similar", 16805, "watch"),
    ]
    similar = segs[3]
    # 4 chats, 0 ventas: no hay datos para pausar; se pausa si llega a 1.5 × costo por venta
    # confirmado de la campaña (148446 / 4 = 37111.5 → 55667).
    assert similar["decision"] == {"action": "watch", "reason": "few_chats", "pause_at_cop": 55667}
    # Intereses ya vendió → vigilar sin umbral de pausa.
    assert segs[2]["decision"] == {"action": "watch", "reason": "few_chats", "pause_at_cop": None}
    assert segs[0]["potential_roas"] == _q(167740, 66972)


def test_anuncios_sin_entrega_no_se_tocan_y_el_resto_decide() -> None:
    ads = {a["id"]: a for a in _score()["ads"]}
    assert ads[AD_PERSO_CARRUSEL]["decision"]["action"] == "keep_pending"
    assert ads[AD_ABIERTA_VIDEO]["decision"]["action"] == "keep_pending"
    assert ads[AD_INTERESES_VIDEO]["decision"]["action"] == "watch"
    assert ads[AD_SIMILAR_VIDEO]["decision"] == {"action": "watch", "reason": "few_chats", "pause_at_cop": 55667}
    assert ads[AD_PERSO_IMAGEN]["decision"] == {"action": "watch", "reason": "few_chats", "pause_at_cop": 55667}
    sin_entrega = [a for a in ads.values() if a["decision"]["action"] == "no_delivery"]
    assert len(sin_entrega) == 6  # imagen y carrusel en Abierta/Intereses/Similar: Meta casi no los muestra
    assert ads[AD_PERSO_CARRUSEL]["label"] == "Carrusel / beneficios"
    assert ads[AD_PERSO_CARRUSEL]["segment_label"] == "Personalizada"
    # desgaste: frecuencia real 1.37 (sin serie diaria → la tendencia no se evaluó)
    assert ads[AD_PERSO_CARRUSEL]["fatigue"] == {
        "frequency": _q(4191, 3065), "cost_trend": None, "ctr_trend": None, "trend_evaluated": False,
    }


def test_piezas_agregadas_entre_segmentos() -> None:
    pieces = {p["label"]: p for p in _score()["pieces"]}
    video, carrusel = pieces["Video / Decoración"], pieces["Carrusel / beneficios"]
    assert (video["totals"]["spend_cop"], video["totals"]["chats"], video["totals"]["paid_sales"]) == (79471, 27, 2)
    assert (carrusel["totals"]["spend_cop"], carrusel["totals"]["chats"], carrusel["totals"]["paid_sales"]) == (56223, 13, 2)
    assert carrusel["delivered_in"] == ["Personalizada"]
    assert video["delivered_in"] == ["Abierta", "Intereses", "Similar"]


def test_tarjetas_del_carrusel_por_headline() -> None:
    cards = _score()["cards"]
    assert [(c["headline"], c["chats"], c["orders"], c["paid_sales"]) for c in cards] == [
        ("Chatea con nosotros", 7, 0, 0),
        ("Velas aromáticas", 6, 3, 2),
    ]
    assert {c["ad_id"] for c in cards} == {AD_PERSO_CARRUSEL}


def test_hallazgos_priorizados() -> None:
    f = _score()["findings"]
    assert [(x["kind"], x["target"]["label"]) for x in f] == [
        ("campaign_verdict", "Liliana / Promocional  / Interacción / CBO / Advantage+ / Haloween"),
        ("replace_card", "Chatea con nosotros"),
        ("follow_up", "Liliana / Promocional  / Interacción / CBO / Advantage+ / Haloween"),
        ("compare_pieces", "Carrusel / beneficios"),
        ("watch", "Similar"),
        ("watch", "Imagen / Beneficios"),
        ("watch", "Intereses"),
    ]
    card = f[1]
    assert card["target"]["segment"] == "Personalizada"
    assert card["evidence"] == {"chats": 7, "orders": 0, "sibling_headline": "Velas aromáticas",
                                "sibling_chats": 6, "sibling_orders": 3}
    assert card["confidence"] == "media"
    follow = f[2]
    assert follow["evidence"] == {"stale_open_chats": 14, "recent_open_chats": 9,
                                  "by_segment": [["Abierta", 6], ["Personalizada", 5], ["Similar", 2], ["Intereses", 1]]}
    cmp_ = f[3]
    assert cmp_["evidence"]["worse_label"] == "Video / Decoración"
    assert cmp_["evidence"]["segments_without_best"] == ["Abierta", "Intereses", "Similar"]
    assert cmp_["confidence"] == "baja"
    assert f[0]["confidence"] == "alta"
    assert f[5]["target"]["segment"] == "Personalizada"


def test_advertencias_de_datos() -> None:
    w = _score()["warnings"]
    assert [x["code"] for x in w] == [
        "orders_stale", "pending_confirmation", "cancelled_excluded", "recent_open", "no_daily_series",
    ]
    assert w[1]["detail"] == {"pending_sales": 1, "pending_revenue_cop": 45000,
                              "unverified_sales": 1, "unverified_revenue_cop": 61940}
    assert w[2]["detail"] == {"cancelled_sales": 1, "cancelled_revenue_cop": 49500}
    assert w[3]["detail"] == {"chats": 9, "since": "2026-09-24"}
    assert w[4]["detail"] == {"ads": 5}


def test_determinista() -> None:
    assert _score() == _score()
