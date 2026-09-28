"""Decisiones del agente que hoy toma el LLM por instrucción del prompt
(diseño v2 §07, familia D; fase F8). Nacen en `reglas` = el LLM decide como
hoy; con Jev, una pregunta cerrada decide ANTES de gastar el turno.

* contactar — «¿Sobra un mensaje proactivo ahora?», antes de redactar el
  gancho de remarketing. Si sobra, no se redacta nada: desaparecen las fugas
  por deliberación del turno de remarketing. Si no sobra (o duda), el LLM
  sigue pudiendo abstenerse como hoy.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales.decisions.capabilities.agente import CONTACTAR, Contacto
from src.sdk.connectorkit import PerceptionResult, TypedAnswer

TRANSCRIPT = "[cliente] Ya les hice el pedido por la web, gracias\n[asesor] ¡Gracias a ti! 🤍"


def _result(*answers: TypedAnswer) -> PerceptionResult:
    return PerceptionResult(ok=True, answers=answers, provider="fake", model="typesafe/jev-1.13-20260917")


def _noul(qid: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="noul", p=p)


def test_contactar_rule_leaves_it_to_the_llm() -> None:
    assert CONTACTAR.rule(Contacto(transcript=TRANSCRIPT, touch_number=2, silence_minutes=125)) is False


def test_contactar_asks_with_the_conversation_and_the_touch_in_view() -> None:
    state, [question] = CONTACTAR.ask(Contacto(transcript=TRANSCRIPT, touch_number=2, silence_minutes=125))

    assert question.id == "contactar.sobra" and question.kind == "noul"
    assert TRANSCRIPT in state and "toque 2" in state and "125 minutos" in state


def test_without_a_conversation_there_is_nothing_to_judge() -> None:
    assert CONTACTAR.ask(Contacto(transcript="", touch_number=1, silence_minutes=None)) is None


def test_jev_says_the_touch_is_not_needed_or_leaves_it_to_the_llm() -> None:
    inp = Contacto(transcript=TRANSCRIPT, touch_number=2, silence_minutes=125)

    assert CONTACTAR.decide(inp, _result(_noul("contactar.sobra", 0.93)), False, {}) is True
    assert CONTACTAR.decide(inp, _result(_noul("contactar.sobra", 0.04)), False, {}) is False
    assert CONTACTAR.decide(inp, _result(_noul("contactar.sobra", 0.5)), False, {}) is None
