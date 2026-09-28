"""`numbers-qa` sobre el SCORECARD (rediseño 2026-09-25): el no-self-review ahora también
reconcilia el drill-down. Recomputa las métricas de campaña/segmento/anuncio con la MISMA
tool (`entity-economics`) y verifica que la jerarquía cuadre (anuncios = segmento,
segmentos = campaña). Un número editado a mano o un anuncio perdido → violación."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from graphs.ctwa_scorecard import run as score
from graphs.numbers_qa import run
from tools.blended_unit_economics.impl import run as blended_unit_economics
from tools.entity_economics.impl import run as entity_economics

GA = Path(__file__).resolve().parents[2]
BREAKDOWN = json.loads((GA / "fixtures" / "halloween_breakdown.json").read_text(encoding="utf-8"))
TOOLS = {"blended-unit-economics": blended_unit_economics, "entity-economics": entity_economics}


def _scorecard() -> dict:
    return score({"breakdown": BREAKDOWN}, tools={"entity-economics": entity_economics})["scorecard"]


def test_scorecard_real_reconcilia() -> None:
    assert run({"days": [], "scorecard": _scorecard()}, tools=TOOLS) == {"passed": True, "violations": []}


def test_sin_scorecard_no_audita_nada_extra() -> None:
    assert run({"days": [], "scorecard": None}, tools=TOOLS) == {"passed": True, "violations": []}


def test_retorno_de_un_segmento_editado_a_mano() -> None:
    sc = _scorecard()
    sc["segments"][0]["metrics"]["roas"] = "3.5"  # Personalizada "mejorada" a mano
    out = run({"days": [], "scorecard": sc}, tools=TOOLS)
    assert out["passed"] is False
    assert {"date": "segmento Personalizada", "issue": "métricas no reconcilian (¿número editado a mano?)"} in out["violations"]


def test_la_jerarquia_debe_cuadrar() -> None:
    sc = copy.deepcopy(_scorecard())
    sc["ads"] = [a for a in sc["ads"] if a["label"] != "Imagen / Beneficios" or a["segment_label"] != "Personalizada"]
    out = run({"days": [], "scorecard": sc}, tools=TOOLS)
    assert out["passed"] is False
    assert any(v["date"] == "segmento Personalizada" and "no suman" in v["issue"] for v in out["violations"])


def test_los_segmentos_deben_sumar_la_campana() -> None:
    sc = _scorecard()
    sc["campaign"]["totals"]["paid_sales"] = 6  # inflar ventas de la campaña
    out = run({"days": [], "scorecard": sc}, tools=TOOLS)
    assert out["passed"] is False
    assert any(v["date"] == "campaña" and "no suman" in v["issue"] for v in out["violations"])
