"""Arena de los clasificadores (plan §5 punto 8 y PR 15), sin etiquetado
humano: latencia p95, caídas a "turno como hoy", costo por turno (LLM +
clasificador) y decisiones de la verificación, desde las trazas de un brazo.
El acuerdo con los asuntos del juez y la calibración (Brier y ECE) son
funciones puras que el scorecard alimenta con los asuntos que el juez saca
por su cuenta."""
from __future__ import annotations

import pytest

from src.plugins.chats.agent.sales_lab.run.arena import arena_metrics, calibration, topic_agreement


def _result(*, perception_ms=300, fallback=None, verify=("send", 120), llm=0.01, pcost=0.0001, extra=False,
            complement=None, error=None) -> dict:
    steps = [{"kind": "perception", "dur_ms": perception_ms, "fallback": fallback, "cost_usd": pcost}]
    if extra:
        steps.append({"kind": "guard", "name": "turn_policy_extra_round"})
    if verify:
        steps.append({"kind": "verify", "decision": verify[0], "dur_ms": verify[1], "cost_usd": pcost, "applied": True})
    return {
        "error": error,
        "llm_cost_usd": llm,
        "trace": None if error else {"mode": "on", "steps": steps, "sent_texts": ["x"]},
        "complement_trace": complement,
    }


def test_arena_metrics_of_a_new_bot_arm() -> None:
    results = [
        _result(perception_ms=200),
        _result(perception_ms=400, extra=True),
        _result(perception_ms=2000, fallback="timeout", verify=None),
        _result(verify=("complement", 150), complement={"steps": [], "sent_texts": ["y"]}),
        _result(error="el turno no terminó en 600 s"),
    ]

    m = arena_metrics(results)

    assert m["turns"] == 4 and m["errors"] == 1
    assert m["perception"]["fallback_rate"] == pytest.approx(0.25)
    assert m["perception"]["p95_ms"] == 2000 and m["perception"]["p50_ms"] == 300
    assert m["verify"]["decisions"] == {"send": 2, "complement": 1}
    assert m["complement_rate"] == pytest.approx(0.25)
    assert m["extra_round_rate"] == pytest.approx(0.25)
    # costo por turno = LLM + clasificador (percepción + verificación)
    assert m["cost_per_turn_usd"] == pytest.approx((4 * 0.01 + 4 * 0.0001 + 3 * 0.0001) / 4)
    assert m["perception_cost_per_turn_usd"] == pytest.approx((4 * 0.0001 + 3 * 0.0001) / 4)


def test_the_current_bot_has_cost_but_no_classifier() -> None:
    m = arena_metrics([{"error": None, "llm_cost_usd": 0.02, "trace": {"mode": "off", "steps": []}}])

    assert m["turns"] == 1 and m["perception"] is None and m["verify"] is None
    assert m["cost_per_turn_usd"] == pytest.approx(0.02)


def test_no_turns_gives_empty_metrics_not_a_division_by_zero() -> None:
    m = arena_metrics([])

    assert m["turns"] == 0 and m["cost_per_turn_usd"] is None


def test_topic_agreement_with_the_judge() -> None:
    a = topic_agreement({"catalogo", "envio"}, {"catalogo", "precio"})

    assert (a["precision"], a["recall"]) == (0.5, 0.5)
    assert a["f1"] == pytest.approx(0.5)
    assert topic_agreement(set(), set()) == {"precision": 1.0, "recall": 1.0, "f1": 1.0}


def test_calibration_brier_and_ece() -> None:
    perfect = calibration([(1.0, True), (0.0, False)])
    assert perfect == {"n": 2, "brier": 0.0, "ece": 0.0}

    over = calibration([(0.9, False), (0.9, False), (0.9, True), (0.9, False)])
    assert over["brier"] == pytest.approx((0.81 * 3 + 0.01) / 4)
    assert over["ece"] == pytest.approx(0.65)  # un solo bin: |0.9 − 0.25|
    assert calibration([]) == {"n": 0, "brier": None, "ece": None}


# ── Acuerdo con el juez (PR 13): los asuntos del juez (EST-08, texto libre)
# se llevan a los 17 códigos del clasificador por palabras clave. Es un mapeo
# aproximado: lo que no calza queda fuera y se cuenta.

def test_judge_topics_map_to_the_classifier_codes() -> None:
    from src.plugins.chats.agent.sales_lab.run.arena import judge_topic_codes

    codes, unmapped = judge_topic_codes(
        [{"topic": "Ver el catálogo de velas"}, {"topic": "costo del envío a Bogotá"}, {"topic": "algo raro"}]
    )

    assert codes == {"catalogo", "envio"} and unmapped == 1


def test_perceived_topics_come_from_the_perception_step() -> None:
    from src.plugins.chats.agent.sales_lab.run.arena import perceived_topics

    trace = {"steps": [{"kind": "perception", "answers": [
        {"q": "topic.catalogo", "type": "noul", "p": 0.92, "picked": True},
        {"q": "topic.envio", "type": "noul", "p": 0.4, "picked": False},
        {"q": "stage", "type": "choice", "choice": "descubrimiento"},
    ]}]}

    assert perceived_topics(trace) == {"catalogo": (0.92, True), "envio": (0.4, False)}
    assert perceived_topics({"steps": []}) is None


def test_topic_arena_measures_agreement_and_calibration_against_the_judge() -> None:
    from src.plugins.chats.agent.sales_lab.run.arena import topic_arena

    pairs = [
        ({"catalogo": (0.9, True), "envio": (0.2, False)}, {"catalogo", "envio"}),
        ({"catalogo": (0.1, False), "envio": (0.95, True)}, {"envio"}),
    ]

    out = topic_arena(pairs)

    assert out["turns"] == 2
    assert (out["precision"], out["recall"]) == (1.0, pytest.approx(2 / 3))
    assert out["calibration"]["n"] == 4
    assert topic_arena([]) == {"turns": 0, "precision": None, "recall": None, "f1": None, "calibration": {"n": 0, "brier": None, "ece": None}}
