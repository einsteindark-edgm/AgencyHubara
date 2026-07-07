"""Golden compartido de la matriz de reactivación (paridad hubara↔GraphAgents).

`fixtures/reengagement_matrix_golden.json` es la ÚNICA tabla de la precedencia
del Free-First Funnel. Este test la corre contra `decide_reengagement` REAL —
hubara es la autoridad. GraphAgents tiene una copia byte-a-byte del JSON
(`GraphAgents/fixtures/reengagement_matrix_golden.json`) que alimenta el golden
de su tool `parse-conversations`; el guard de checksum en AMBOS lados detecta
el drift (la frontera del monorepo impide compartir el archivo por import).

Si cambias la política en `send_policy.py`: actualiza el JSON acá, re-copia a
GraphAgents, y actualiza `MATRIX_SHA256` en los DOS tests de checksum.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.platform.whatsapp.cost import RateCard, RateCardEntry
from src.platform.whatsapp.send_policy import LeadState, decide_reengagement

FIXTURE = Path(__file__).parent / "fixtures" / "reengagement_matrix_golden.json"

#: sha256 de la matriz. El MISMO valor vive en el test de checksum del lado
#: GraphAgents (tests/tools/test_parse_conversations.py). Editar la matriz sin
#: sincronizar ambos = rojo en los dos lados, a propósito.
MATRIX_SHA256 = "3a56faa53fb38e7d495221bb3015d863289fc51af2d0b4c1b94eb71894452183"


def _load() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _rate_card(spec: dict) -> RateCard:
    return RateCard(
        version=spec["version"],
        effective_from_ms=spec["effective_from_ms"],
        country=spec["country"],
        currency=spec["currency"],
        rates={
            cat: RateCardEntry(usd_micros_per_message=micros)
            for cat, micros in spec["rates"].items()
        },
    )


_MATRIX = _load()


@pytest.mark.parametrize(
    "case", _MATRIX["cases"], ids=[c["name"] for c in _MATRIX["cases"]]
)
def test_matrix_case_matches_decide_reengagement(case: dict):
    decision = decide_reengagement(
        _MATRIX["now_ms"],
        case["metadata"],
        LeadState(**case["lead"]),
        _rate_card(_MATRIX["rate_card"]),
    )
    expect = case["expect"]
    assert decision.allowed is expect["allowed"], case["name"]
    assert decision.channel == expect["channel"], case["name"]
    assert decision.recommended_category == expect["recommended_category"]
    assert decision.is_free is expect["is_free"], case["name"]
    assert decision.expected_cost_micros == expect["expected_cost_micros"]
    assert decision.suppress_reason == expect["suppress_reason"], case["name"]


def test_matrix_checksum_synced_with_graphagents_copy():
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert digest == MATRIX_SHA256, (
        "La matriz cambió: re-copia el JSON a GraphAgents/fixtures/ y "
        "actualiza MATRIX_SHA256 en ambos tests de checksum."
    )
