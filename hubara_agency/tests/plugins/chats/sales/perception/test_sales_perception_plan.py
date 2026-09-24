"""Capas ① y ③ del turno de ventas (plan del laboratorio §3.2 y §4.2). PURO.

① Antes del LLM: el clasificador responde el juego `rafaga-v1` sobre la
ráfaga (asuntos, hilo, etapa y asunto principal de cada mensaje) y el plan
arma la lista de asuntos que el turno tiene que atender, con el mensaje de
cada uno. ② Durante: si el corte por tool deja un asunto del plan sin
atender, una ronda más (regla determinista, sin llamadas nuevas). ③ Al
final: el clasificador verifica la respuesta; si falta un asunto con
claridad, un complemento; si hay duda, se envía y queda pendiente.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from src.plugins.chats.agent.sales.perception.plan import (
    DEFAULT_THRESHOLDS,
    PlanTopic,
    TurnPlan,
    checklist_note,
    complement_message,
    coverage_decision,
    plan_from_answers,
    uncovered_topics,
)
from src.plugins.chats.agent.sales.perception.questions import (
    STAGE_OPTIONS,
    TOPICS,
    burst_state,
    rafaga_questions,
    verify_questions,
)
from src.sdk.connectorkit import PerceptionResult, TypedAnswer

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
    questions = rafaga_questions(MESSAGES)
    ids = [q.id for q in questions]

    assert [f"topic.{t}" for t in TOPICS] == [i for i in ids if i.startswith("topic.")]
    assert len(TOPICS) == 17
    assert {"thread.answers_last_bot_question", "thread.repeats_unanswered", "stage"} <= set(ids)
    assert ["msg.1.topic", "msg.2.topic"] == [i for i in ids if i.startswith("msg.")]
    stage = next(q for q in questions if q.id == "stage")
    assert stage.kind == "choice" and stage.options == STAGE_OPTIONS and len(STAGE_OPTIONS) == 7
    assert all(q.kind == "noul" for q in questions if q.id.startswith(("topic.", "thread.")))
    assert set(next(q for q in questions if q.id == "msg.1.topic").options) == {*TOPICS, "ninguno"}


def test_the_state_shows_each_message_with_its_offset_and_the_pending_topics() -> None:
    state = burst_state(MESSAGES, pending=("pagos",), last_bot_text="¿Para qué ocasión es?")

    assert "[1] (+0 s) vi que hacen velas con otros diseños, ¿me mandas el catálogo?" in state
    assert "[2] (+7 s) y el envío a Bogotá cuánto sale?" in state
    assert "pagos" in state and "¿Para qué ocasión es?" in state


def test_the_thresholds_match_the_versioned_profiles() -> None:
    profiles = yaml.safe_load(Path("src/platform/perception/profiles.yaml").read_text(encoding="utf-8"))

    for profile in ("jev-v1", "openai-lp-v1"):
        assert profiles[profile]["thresholds"] == DEFAULT_THRESHOLDS, profile


# ── ① plan ───────────────────────────────────────────────────────────────────


def test_the_plan_lists_each_detected_topic_with_its_message() -> None:
    result = _result(
        _noul("topic.catalogo", 0.96), _noul("topic.envio", 0.93), _noul("topic.precio", 0.40),
        _choice("msg.1.topic", "catalogo"), _choice("msg.2.topic", "envio"), _choice("stage", "descubrimiento"),
    )

    plan = plan_from_answers(result, n_messages=2)

    assert plan.ok and [(t.topic, t.msg) for t in plan.topics] == [("catalogo", 1), ("envio", 2)]
    assert plan.stage == "descubrimiento"


def test_a_failed_classifier_gives_an_empty_plan_and_the_turn_runs_as_today() -> None:
    plan = plan_from_answers(_result(ok=False), n_messages=2)

    assert not plan.ok and plan.topics == ()
    assert checklist_note(plan) is None


def test_the_checklist_note_asks_the_llm_to_answer_every_topic() -> None:
    plan = TurnPlan(ok=True, topics=(PlanTopic("catalogo", 1, 0.96), PlanTopic("envio", 2, 0.93)))

    note = checklist_note(plan)

    assert note is not None and "catálogo (mensaje 1)" in note and "envío (mensaje 2)" in note


# ── ② ronda extra ────────────────────────────────────────────────────────────


def test_a_tool_that_cut_the_turn_leaves_the_catalog_uncovered() -> None:
    plan = TurnPlan(ok=True, topics=(PlanTopic("catalogo", 1, 0.96), PlanTopic("envio", 2, 0.93)))

    missing = uncovered_topics(plan, tools_used=["send_shipping_rates"], text="")

    assert [t.topic for t in missing] == ["catalogo"]
    assert uncovered_topics(plan, tools_used=["send_shipping_rates", "present_products"], text="") == []
    assert uncovered_topics(plan, tools_used=["send_shipping_rates"], text="Te dejo nuestro catálogo 👇") == []


# ── ③ verificación ───────────────────────────────────────────────────────────


def test_verify_asks_one_question_per_planned_topic() -> None:
    plan = TurnPlan(ok=True, topics=(PlanTopic("catalogo", 1, 0.96), PlanTopic("envio", 2, 0.93)))

    questions = verify_questions(plan)

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

    text = complement_message(plan, ("catalogo",))

    assert text.startswith("[SISTEMA]") and "catálogo" in text and "mensaje 1" in text
    assert "send_reply" in text
