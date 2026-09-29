"""Cuestionarios como DATOS (motor de decisiones, diseño v2 §03).

Una pregunta nueva va a un cuestionario versionado (`questions/<id>.yaml`),
no al workflow ni a las tools. `rafaga-v1` es el cuestionario de hoy: pasado a
datos tiene que preguntar EXACTAMENTE lo mismo que el código anterior (la
lista quedó congelada en `tests/fixtures/decisions/rafaga_v1_frozen.json`),
porque el laboratorio y la sombra se miden contra esas preguntas.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from src.plugins.chats.agent.sales.decisions.plan import PlanTopic, TurnPlan
from src.plugins.chats.agent.sales.decisions.questionnaire import load_questionnaire

FROZEN = json.loads(
    (Path(__file__).resolve().parents[4] / "fixtures/decisions/rafaga_v1_frozen.json").read_text(encoding="utf-8")
)
MSGS = [{"text": "hola", "ts_ms": 1000}, {"text": "precio?", "ts_ms": 4000}, {"text": "y envío", "ts_ms": 9000}]


def _plain(q) -> dict:
    crit = dict(q.criteria) if isinstance(q.criteria, Mapping) else list(q.criteria)
    return {"id": q.id, "kind": q.kind, "text": q.text, "criteria": crit}


@pytest.mark.parametrize("n", [0, 1, 2, 3])
def test_rafaga_v1_asks_exactly_what_the_code_asked(n: int) -> None:
    rafaga = load_questionnaire("rafaga-v1")

    assert [_plain(q) for q in rafaga.burst_questions(MSGS[:n])] == FROZEN["rafaga"][str(n)]


def test_rafaga_v1_verifies_exactly_what_the_code_verified() -> None:
    rafaga = load_questionnaire("rafaga-v1")
    topics = tuple(PlanTopic(t, (i % 3) or None, 0.9) for i, t in enumerate(rafaga.topic_ids))

    assert [_plain(q) for q in rafaga.verify_questions(TurnPlan(ok=True, topics=topics))] == FROZEN["verify"]


def test_rafaga_v1_states_are_the_same_text() -> None:
    rafaga = load_questionnaire("rafaga-v1")

    assert rafaga.burst_state(MSGS, pending=("pagos",), last_bot_text="¿Para qué ocasión es?" * 30) == FROZEN["burst_state"]
    assert rafaga.burst_state(MSGS[:1]) == FROZEN["burst_state_plain"]
    assert rafaga.reply_state(MSGS, "Te dejo el catálogo", ["present_products"]) == FROZEN["reply_state"]
    assert rafaga.reply_state(MSGS[:1], "  ", []) == FROZEN["reply_state_empty"]


def test_the_topic_labels_come_from_the_questionnaire() -> None:
    rafaga = load_questionnaire("rafaga-v1")

    assert rafaga.label("envio") == "envío"
    assert rafaga.label("datos_envio") == "datos de envío"
    assert rafaga.label("no-existe") == "no-existe"
    assert len(rafaga.topic_ids) == 17


def test_an_unknown_questionnaire_is_an_error_at_load_time() -> None:
    with pytest.raises(KeyError, match="rafaga-v0"):
        load_questionnaire("rafaga-v0")


def test_rafaga_v4_asks_about_types_and_collections_of_candles() -> None:
    """Con Jev real (experimento del 2026-09-29, mensajes del laboratorio con
    el anuncio adelante), estas descripciones llevan «catálogo» de 0,11–0,32
    a 0,97 y «disponibilidad» de 0,31 a 0,94, sin temas falsos en los
    controles («Hola», «ok gracias», «¿Qué tamaño son?», «¿cuánto vale…?»)."""
    v3, v4 = load_questionnaire("rafaga-v3"), load_questionnaire("rafaga-v4")
    hints = {t: h for t, _l, h in v4.topics}

    assert "tipo, estilo o colección de velas" in hints["catalogo"]
    assert "si tienen o venden algo" in hints["disponibilidad"]
    # Mismos ids y mismo resto (el banco de referencia califica por id): solo
    # cambian esas dos descripciones.
    assert [t for t, _l, _h in v4.topics] == [t for t, _l, _h in v3.topics]
    assert {k: v for k, v in v4.raw.items() if k not in ("id", "topics")} == {
        k: v for k, v in v3.raw.items() if k not in ("id", "topics")
    }
    changed = {t for (t, _l, h), (_t3, _l3, h3) in zip(v4.topics, v3.topics) if h != h3}
    assert changed == {"catalogo", "disponibilidad"}
