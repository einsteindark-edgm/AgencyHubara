"""Perfil `jev-v2`: cuestionario `rafaga-v2` + política `turno-v2` (diseño v2
§01, fase F1).

Jev lee el «Si» CON contexto: el motor le da lo que el cliente vio antes del
turno y los hechos del pedido en secciones separadas de los mensajes de ESTE
turno, le pregunta qué le había preguntado el asesor y si el cliente lo
responde, y la política convierte eso en una nota para el LLM y en evidencia
para la confirmación de compra. Las preguntas de asuntos se limitan a «este
turno» (si no, Jev tomaría como pedido nuevo algo que ya está en el contexto).
"""
from __future__ import annotations

import pytest

from src.plugins.chats.agent.sales.decisions.context import TurnContext, Window
from src.plugins.chats.agent.sales.decisions.policies import get_policy
from src.plugins.chats.agent.sales.decisions.questionnaire import load_questionnaire
from src.sdk.connectorkit import PerceptionResult, TypedAnswer

RAFAGA = load_questionnaire("rafaga-v2")
POLICY = get_policy("turno-v2")
BURST = [{"text": "Si", "ts_ms": 1_000}, {"text": "Y quiero una vela adicional", "ts_ms": 13_000}]
CONTEXT = TurnContext(
    window=Window(
        lines=(
            "[cliente] Quiero el dúo amarillo aries lavanda",
            "[asesor] …Me acuerdo perfecto de tu Duo Zodiacal Aries. ¿Te lo enviamos a la misma dirección?",
        )
    ),
    facts=("Etapa: datos de envío", "Ítem 1: Duo Zodiacal Aries, amarillo, lavanda, 1 unidad", "Ciudad: Medellín"),
)
TH = {"detect": 0.70, "confidence": 0.60, "covered": 0.70, "purchase_confirm": 0.85, "purchase_retract": 0.20}


def _noul(qid: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="noul", p=p)


def _choice(qid: str, pick: str, dist: dict[str, float]) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="choice", choice=pick, probs=tuple(dist.items()), confidence=dist[pick])


def _result(*answers: TypedAnswer) -> PerceptionResult:
    return PerceptionResult(ok=True, answers=answers, provider="fake", model="typesafe/jev-1.13-20260917")


def test_the_state_separates_context_facts_and_this_turn() -> None:
    state = RAFAGA.burst_state(BURST, context=CONTEXT)

    assert state.index("CONTEXTO") < state.index("HECHOS DEL PEDIDO") < state.index("ESTE TURNO")
    assert "¿Te lo enviamos a la misma dirección?" in state
    assert "Etapa: datos de envío" in state
    assert state.rstrip().endswith("[2] (+12 s) Y quiero una vela adicional")


def test_topic_questions_are_limited_to_this_turn() -> None:
    questions = RAFAGA.burst_questions(BURST, facts={"has_context": True, "bot_asked_known": None})

    envio = next(q for q in questions if q.id == "topic.envio")
    assert "ESTE TURNO" in envio.text


def test_the_thread_questions_ask_what_the_bot_asked_and_what_the_customer_answers() -> None:
    ids = [q.id for q in RAFAGA.burst_questions(BURST, facts={"has_context": True, "bot_asked_known": None})]

    assert {"thread.bot_asked", "thread.answers_bot", "thread.answer"} <= set(ids)
    assert "stage" not in ids  # la etapa la calcula el código; la de Jev no se usaba
    bot_asked = next(q for q in RAFAGA.burst_questions(BURST, facts={"has_context": True, "bot_asked_known": None})
                     if q.id == "thread.bot_asked")
    assert set(bot_asked.options) == {
        "confirmar_compra", "confirmar_dato_envio", "elegir_variante", "ver_opciones", "otra_si_no", "pregunta_abierta", "nada",
    }


def test_when_the_code_knows_what_was_asked_jev_is_not_asked_it() -> None:
    ids = [q.id for q in RAFAGA.burst_questions(BURST, facts={"has_context": True, "bot_asked_known": "confirmar_compra"})]

    assert "thread.bot_asked" not in ids and "thread.answer" in ids


def test_without_context_there_are_no_thread_questions() -> None:
    ids = [q.id for q in RAFAGA.burst_questions(BURST, facts={"has_context": False, "bot_asked_known": None})]

    assert not [i for i in ids if i.startswith("thread.")]


def test_a_yes_to_the_address_question_is_not_a_purchase() -> None:
    result = _result(
        _choice("thread.bot_asked", "confirmar_dato_envio", {"confirmar_dato_envio": 0.9, "confirmar_compra": 0.05, "nada": 0.05}),
        _noul("thread.answers_bot", 0.93),
        _choice("thread.answer", "si", {"si": 0.95, "no": 0.03, "otra": 0.02}),
        _noul("topic.variante", 0.2),
    )

    turn = POLICY.decide_turn(result, questionnaire=RAFAGA, context=CONTEXT, n_messages=2, thresholds=TH)

    assert turn.reading["bot_asked"] == "confirmar_dato_envio"
    assert turn.reading["purchase"] == "no"
    assert turn.note is not None and turn.note.startswith("[LECTURA DEL TURNO]")
    assert "dato de envío" in turn.note and "No es una compra nueva" in turn.note


def test_a_yes_to_the_confirmation_card_is_a_purchase_with_evidence() -> None:
    ctx = TurnContext(window=Window(lines=("[asesor] 🧾 El bot envió el resumen del pedido con botones para confirmar",),
                                    last_component="order_confirmation", bot_asked_known="confirmar_compra"))
    result = _result(_noul("thread.answers_bot", 0.95), _choice("thread.answer", "si", {"si": 0.96, "no": 0.02, "otra": 0.02}))

    turn = POLICY.decide_turn(result, questionnaire=RAFAGA, context=ctx, n_messages=1, thresholds=TH)

    assert turn.reading["bot_asked"] == "confirmar_compra" and turn.reading["bot_asked_by"] == "codigo"
    assert turn.reading["purchase"] == "si"


def test_a_doubtful_reading_leaves_the_rule_of_today() -> None:
    result = _result(
        _choice("thread.bot_asked", "otra_si_no", {"otra_si_no": 0.5, "confirmar_compra": 0.4, "nada": 0.1}),
        _noul("thread.answers_bot", 0.9),
        _choice("thread.answer", "si", {"si": 0.9, "no": 0.05, "otra": 0.05}),
    )

    turn = POLICY.decide_turn(result, questionnaire=RAFAGA, context=CONTEXT, n_messages=1, thresholds=TH)

    assert turn.reading["purchase"] == "duda"


def test_the_note_keeps_the_checklist_of_this_turn() -> None:
    result = _result(
        _choice("thread.bot_asked", "confirmar_dato_envio", {"confirmar_dato_envio": 0.9, "confirmar_compra": 0.05, "nada": 0.05}),
        _noul("thread.answers_bot", 0.9),
        _choice("thread.answer", "si", {"si": 0.95, "no": 0.03, "otra": 0.02}),
        _noul("topic.catalogo", 0.9),
        _choice("msg.2.topic", "catalogo", {"catalogo": 0.9, "ninguno": 0.1}),
    )

    turn = POLICY.decide_turn(result, questionnaire=RAFAGA, context=CONTEXT, n_messages=2, thresholds=TH)

    assert [t["topic"] for t in turn.topics] == ["catalogo"]
    assert "catálogo (mensaje 2)" in turn.note
    assert set(turn.coverage) == {"catalogo"}


@pytest.mark.parametrize("failed", [PerceptionResult(ok=False, error="timeout", provider="x", model="x")])
def test_a_failed_oracle_gives_no_note_and_no_reading(failed: PerceptionResult) -> None:
    turn = POLICY.decide_turn(failed, questionnaire=RAFAGA, context=CONTEXT, n_messages=2, thresholds=TH)

    assert (turn.note, turn.reading, turn.coverage) == (None, {}, {})
