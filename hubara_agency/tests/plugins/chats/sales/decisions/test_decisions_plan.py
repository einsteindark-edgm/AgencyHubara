"""Política `turno-v1` del motor de decisiones (plan del laboratorio §3.2 y
§4.2; diseño v2 §03). PURO.

① Antes del LLM: Jev responde el cuestionario `rafaga-v1` sobre la ráfaga y la
política arma la lista de asuntos que el turno tiene que atender, con el
mensaje de cada uno, la NOTA para el LLM y las REGLAS de la capa ② (qué tool
o qué texto atiende cada asunto). Las reglas viajan grabadas en el resultado
de la activity: el workflow solo las aplica, así cambiarlas nunca rompe el
replay de una conversación en vuelo. ② Durante: si el corte por tool deja un
asunto sin atender con lo que el CLIENTE VE (no la narración descartada), una
ronda más. ③ Al final: Jev verifica la respuesta; si falta un asunto con
claridad, un complemento; si hay duda, se envía y queda pendiente.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from src.plugins.chats.agent.sales.decisions.plan import PlanTopic, TurnPlan, uncovered_topics
from src.plugins.chats.agent.sales.decisions.policies.turno_v1 import (
    DEFAULT_THRESHOLDS,
    checklist_note,
    complement_note,
    coverage_decision,
    coverage_rules,
    plan_from_answers,
    topic_rows,
)
from src.plugins.chats.agent.sales.decisions.questionnaire import load_questionnaire
from src.sdk.connectorkit import PerceptionResult, TypedAnswer

RAFAGA = load_questionnaire("rafaga-v1")
MESSAGES = [
    {"text": "vi que hacen velas con otros diseños, ¿me mandas el catálogo?", "ts_ms": 1_000},
    {"text": "y el envío a Bogotá cuánto sale?", "ts_ms": 8_000},
]


def _noul(qid: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="noul", p=p)


def _choice(qid: str, pick: str, conf: float = 0.9) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="choice", choice=pick, probs=((pick, conf),), confidence=conf)


def _result(*answers: TypedAnswer, ok: bool = True) -> PerceptionResult:
    return PerceptionResult(ok=ok, answers=tuple(answers), provider="fake", model="fake")


# ── ① preguntas ──────────────────────────────────────────────────────────────


def test_rafaga_v1_asks_every_topic_the_thread_the_stage_and_each_message() -> None:
    questions = RAFAGA.burst_questions(MESSAGES)
    ids = [q.id for q in questions]

    assert [f"topic.{t}" for t in RAFAGA.topic_ids] == [i for i in ids if i.startswith("topic.")]
    assert len(RAFAGA.topic_ids) == 17
    assert {"thread.answers_last_bot_question", "thread.repeats_unanswered", "stage"} <= set(ids)
    assert ["msg.1.topic", "msg.2.topic"] == [i for i in ids if i.startswith("msg.")]
    stage = next(q for q in questions if q.id == "stage")
    assert stage.kind == "choice" and len(stage.options) == 7
    assert all(q.kind == "noul" for q in questions if q.id.startswith(("topic.", "thread.")))
    assert set(next(q for q in questions if q.id == "msg.1.topic").options) == {*RAFAGA.topic_ids, "ninguno"}


def test_the_state_shows_each_message_with_its_offset_and_the_pending_topics() -> None:
    state = RAFAGA.burst_state(MESSAGES, pending=("pagos",), last_bot_text="¿Para qué ocasión es?")

    assert "[1] (+0 s) vi que hacen velas con otros diseños, ¿me mandas el catálogo?" in state
    assert "[2] (+7 s) y el envío a Bogotá cuánto sale?" in state
    assert "pagos" in state and "¿Para qué ocasión es?" in state


def test_the_thresholds_match_the_engine_profile() -> None:
    profiles = yaml.safe_load(
        Path("src/plugins/chats/agent/sales/decisions/profiles.yaml").read_text(encoding="utf-8")
    )

    assert profiles["jev-v1"]["thresholds"] == DEFAULT_THRESHOLDS


# ── ① plan ───────────────────────────────────────────────────────────────────


def test_the_plan_lists_each_detected_topic_with_its_message() -> None:
    result = _result(
        _noul("topic.catalogo", 0.96), _noul("topic.envio", 0.93), _noul("topic.precio", 0.40),
        _choice("msg.1.topic", "catalogo"), _choice("msg.2.topic", "envio"), _choice("stage", "descubrimiento"),
    )

    plan = plan_from_answers(result, topics=RAFAGA.topic_ids, n_messages=2)

    assert plan.ok and [(t.topic, t.msg) for t in plan.topics] == [("catalogo", 1), ("envio", 2)]
    assert plan.stage == "descubrimiento"


def test_a_failed_classifier_gives_an_empty_plan_and_the_turn_runs_as_today() -> None:
    plan = plan_from_answers(_result(ok=False), topics=RAFAGA.topic_ids, n_messages=2)

    assert not plan.ok and plan.topics == ()
    assert checklist_note(plan, RAFAGA) is None


def test_the_checklist_note_asks_the_llm_to_answer_every_topic() -> None:
    plan = TurnPlan(ok=True, topics=(PlanTopic("catalogo", 1, 0.96), PlanTopic("envio", 2, 0.93)))

    note = checklist_note(plan, RAFAGA)

    assert note is not None and "catálogo (mensaje 1)" in note and "envío (mensaje 2)" in note


# ── ② ronda extra ────────────────────────────────────────────────────────────


def test_a_tool_that_cut_the_turn_leaves_the_catalog_uncovered() -> None:
    plan = TurnPlan(ok=True, topics=(PlanTopic("catalogo", 1, 0.96), PlanTopic("envio", 2, 0.93)))
    rules = coverage_rules(plan)

    missing = uncovered_topics(plan.topics, rules, tools_used=["send_shipping_rates"], shown="")

    assert [t.topic for t in missing] == ["catalogo"]
    assert uncovered_topics(plan.topics, rules, tools_used=["send_shipping_rates", "present_products"], shown="") == []
    assert uncovered_topics(plan.topics, rules, tools_used=["send_shipping_rates"], shown="Te dejo nuestro catálogo 👇") == []


def test_the_rules_of_the_extra_round_travel_as_data() -> None:
    """Lo que la capa ② aplica viaja en el resultado grabado de la activity:
    solo los asuntos del plan, en JSON."""
    plan = TurnPlan(ok=True, topics=(PlanTopic("envio", 2, 0.93), PlanTopic("aplaza", 1, 0.9)))

    rules = coverage_rules(plan)

    assert set(rules) == {"envio", "aplaza"}
    assert "send_shipping_rates" in rules["envio"]["tools"] and rules["envio"]["any_text"] is False
    assert rules["aplaza"] == {"tools": [], "words": [], "any_text": True}


def test_a_deferral_is_covered_only_by_a_text_the_customer_sees() -> None:
    """Bug de #372 (sección 9 del diseño): `aplaza` tenía la palabra vacía y
    quedaba SIEMPRE cubierto. El cliente que aplaza espera una frase."""
    plan = TurnPlan(ok=True, topics=(PlanTopic("aplaza", 1, 0.9),))
    rules = coverage_rules(plan)

    assert [t.topic for t in uncovered_topics(plan.topics, rules, tools_used=["send_shipping_rates"], shown="")] == ["aplaza"]
    assert uncovered_topics(plan.topics, rules, tools_used=[], shown="Listo, aquí estamos cuando quieras") == []


def test_the_topics_carry_the_label_the_workflow_writes_in_its_notes() -> None:
    plan = TurnPlan(ok=True, topics=(PlanTopic("catalogo", 1, 0.96), PlanTopic("envio", None, 0.93)))

    assert topic_rows(plan, RAFAGA) == [
        {"topic": "catalogo", "msg": 1, "p": 0.96, "label": "catálogo (mensaje 1)"},
        {"topic": "envio", "msg": None, "p": 0.93, "label": "envío"},
    ]


# ── ③ verificación ───────────────────────────────────────────────────────────


def test_verify_asks_one_question_per_planned_topic() -> None:
    plan = TurnPlan(ok=True, topics=(PlanTopic("catalogo", 1, 0.96), PlanTopic("envio", 2, 0.93)))

    questions = RAFAGA.verify_questions(plan)

    assert [q.id for q in questions] == ["cover.catalogo", "cover.envio"]
    assert all(q.kind == "noul" for q in questions)


def test_coverage_decision_sends_complements_or_leaves_pending() -> None:
    plan = TurnPlan(ok=True, topics=(PlanTopic("catalogo", 1, 0.96), PlanTopic("envio", 2, 0.93)))

    ok = coverage_decision(plan, _result(_noul("cover.catalogo", 0.95), _noul("cover.envio", 0.9)))
    missing = coverage_decision(plan, _result(_noul("cover.catalogo", 0.05), _noul("cover.envio", 0.9)))
    unsure = coverage_decision(plan, _result(_noul("cover.catalogo", 0.5), _noul("cover.envio", 0.9)))
    down = coverage_decision(plan, _result(ok=False))

    assert (ok.decision, ok.missing) == ("send", ())
    assert (missing.decision, missing.missing) == ("complement", ("catalogo",))
    assert (unsure.decision, unsure.missing) == ("pending", ("catalogo",))
    assert down.decision == "send"  # sin verificación, el turno sale como hoy


def test_the_complement_is_one_short_bubble_about_what_was_missing() -> None:
    plan = TurnPlan(ok=True, topics=(PlanTopic("catalogo", 1, 0.96), PlanTopic("envio", 2, 0.93)))

    text = complement_note(plan, ("catalogo",), RAFAGA)

    assert text.startswith("[SISTEMA]") and "catálogo" in text and "mensaje 1" in text
    assert "send_reply" in text
