"""`ctwa-scorecard` — desgaste de un anuncio (punto 5 del rediseño 2026-09-25).

El "rotate_creative" viejo salía del MER DIARIO de la cuenta (ventas de toda la tienda)
y marcaba desgaste falso. Ahora el desgaste se mide POR ANUNCIO con dos señales:
frecuencia (impresiones/alcance ≥ 3) y tendencia de la serie diaria (costo por
conversación sube ≥30% o el CTR cae ≥30% entre la primera y la segunda mitad)."""
from __future__ import annotations

from graphs.ctwa_scorecard import run
from tools.entity_economics.impl import run as entity_economics


def _ad(ad_id: str, *, spend: int, reach: int, impressions: int, daily: list, chats: int = 12) -> dict:
    return {
        "ad_id": ad_id, "ad_name": f"Marca / Presentación / Video / {ad_id}", "adset_id": "s1",
        "adset_name": "MARCA / abierta / Conversiones",
        "meta": {"spend_cop": spend, "impressions": impressions, "reach": reach,
                 "link_clicks": 200, "conversations": 40},
        "daily": daily,
        "chats": [{"date": "2026-09-02", "headline": "Velas", "state": "no_reply", "order": None}] * chats,
    }


def _day(date: str, spend: int, conv: int, clicks: int = 25, impressions: int = 1000) -> dict:
    return {"date": date, "spend_cop": spend, "impressions": impressions, "link_clicks": clicks,
            "conversations": conv}


def _breakdown(*ads: dict) -> dict:
    return {"schema": 1, "campaign": {"id": "c1", "name": "Campaña", "budget_level": "adset"},
            "window": {"since": "2026-09-01", "until": "2026-09-08", "timezone": "America/Bogota"},
            "orders_stale": False, "ads": list(ads)}


def _ads(breakdown: dict) -> dict:
    sc = run({"breakdown": breakdown}, tools={"entity-economics": entity_economics})["scorecard"]
    return {a["id"]: a for a in sc["ads"]}


ESTABLE = [_day(f"2026-09-0{i}", 10000, 5) for i in range(1, 9)]
# misma plata, la mitad de conversaciones en la segunda mitad → el costo por chat se duplica
CARO = [_day(f"2026-09-0{i}", 10000, 6 if i <= 4 else 3) for i in range(1, 9)]
# el CTR cae de 4% a 2% (mismas impresiones, la mitad de clics)
CTR_CAE = [_day(f"2026-09-0{i}", 10000, 5, clicks=40 if i <= 4 else 20) for i in range(1, 9)]


def test_frecuencia_alta_es_desgaste() -> None:
    ads = _ads(_breakdown(_ad("a1", spend=80000, reach=2000, impressions=7000, daily=ESTABLE)))
    assert ads["a1"]["decision"] == {"action": "rotate_creative", "reason": "frequency"}
    assert ads["a1"]["fatigue"]["frequency"] == "3.5"


def test_costo_por_conversacion_que_sube_es_desgaste() -> None:
    ads = _ads(_breakdown(_ad("a1", spend=80000, reach=6000, impressions=8000, daily=CARO)))
    assert ads["a1"]["decision"] == {"action": "rotate_creative", "reason": "cost_trend"}
    # 1ra mitad 40000/24 → 2da 40000/12: el costo por conversación se duplica
    assert ads["a1"]["fatigue"]["cost_trend"] == "2"
    assert ads["a1"]["fatigue"]["trend_evaluated"] is True


def test_ctr_que_cae_es_desgaste() -> None:
    ads = _ads(_breakdown(_ad("a1", spend=80000, reach=6000, impressions=8000, daily=CTR_CAE)))
    assert ads["a1"]["decision"] == {"action": "rotate_creative", "reason": "ctr_trend"}
    assert ads["a1"]["fatigue"]["ctr_trend"] == "0.5"


def test_serie_estable_no_es_desgaste() -> None:
    ads = _ads(_breakdown(_ad("a1", spend=80000, reach=6000, impressions=8000, daily=ESTABLE)))
    assert ads["a1"]["decision"]["action"] != "rotate_creative"
    assert ads["a1"]["fatigue"]["cost_trend"] == "1"


def test_serie_corta_no_se_evalua() -> None:
    # < 4 días o < 3 conversaciones por mitad: no hay tendencia honesta que medir.
    corta = [_day("2026-09-01", 10000, 5), _day("2026-09-02", 10000, 1)]
    ads = _ads(_breakdown(_ad("a1", spend=20000, reach=6000, impressions=8000, daily=corta)))
    assert ads["a1"]["fatigue"]["trend_evaluated"] is False
    assert ads["a1"]["fatigue"]["cost_trend"] is None
