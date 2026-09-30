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
    state, [question, _ended] = CONTACTAR.ask(Contacto(transcript=TRANSCRIPT, touch_number=2, silence_minutes=125))

    assert question.id == "contactar.sobra" and question.kind == "noul"
    assert TRANSCRIPT in state and "toque 2" in state and "125 minutos" in state


def test_contactar_names_a_conversation_that_already_ended() -> None:
    """El operador (2026-09-30): remarketing no reconoce que la conversación
    terminó (el cliente ya recibió su pedido y solo agradeció)."""
    _state, [question, _ended] = CONTACTAR.ask(Contacto(transcript=TRANSCRIPT, touch_number=1, silence_minutes=240))

    assert "recibió su pedido" in question.text and "solo agradeció" in question.text


def test_contactar_also_asks_whether_the_conversation_ended() -> None:
    """Con Jev real (casos emulados, 2026-09-30), «¿sobra un mensaje
    proactivo?» dudaba donde la conversación ya terminó (0,74 a 0,84); «¿la
    conversación ya terminó?» separa: 0,91 tras la entrega y el gracias, 0,05
    con una venta abierta. Cualquiera de las dos, segura, salta el gancho."""
    inp = Contacto(transcript=TRANSCRIPT, touch_number=1, silence_minutes=240)

    _state, questions = CONTACTAR.ask(inp)

    assert [q.id for q in questions] == ["contactar.sobra", "contactar.terminada"]
    ended = _result(_noul("contactar.sobra", 0.74), _noul("contactar.terminada", 0.91))
    open_sale = _result(_noul("contactar.sobra", 0.2), _noul("contactar.terminada", 0.05))
    assert CONTACTAR.decide(inp, ended, False, {}) is True
    assert CONTACTAR.decide(inp, open_sale, False, {}) is None


def test_without_a_conversation_there_is_nothing_to_judge() -> None:
    assert CONTACTAR.ask(Contacto(transcript="", touch_number=1, silence_minutes=None)) is None


def test_jev_says_the_touch_is_not_needed_or_leaves_it_to_the_llm() -> None:
    inp = Contacto(transcript=TRANSCRIPT, touch_number=2, silence_minutes=125)

    assert CONTACTAR.decide(inp, _result(_noul("contactar.sobra", 0.93)), False, {}) is True
    assert CONTACTAR.decide(inp, _result(_noul("contactar.sobra", 0.04)), False, {}) is False
    assert CONTACTAR.decide(inp, _result(_noul("contactar.sobra", 0.5)), False, {}) is None


# ── cierre por abandono (F8): la etiqueta del ghosting ──
#
# Hoy la decide el LLM con el aviso de ghosting (4 etiquetas; una regla
# degrada después). Con Jev, la lectura de la conversación la decide y el
# aviso le dice al LLM cuál usar (el LLM solo ejecuta la tool: la mecánica
# del cierre queda igual). El código pone los invariantes: pedido registrado
# = COMPRA_EXITOSA; CONFIRMADO_SIN_DATOS sin confirmación = INTERESADO.

from src.plugins.chats.agent.sales.decisions.capabilities.agente import CIERRE, Abandono  # noqa: E402


def _tag(choice: str, p: float) -> TypedAnswer:
    return TypedAnswer(id="cierre.etiqueta", kind="choice", choice=choice, probs=((choice, p),), confidence=p)


def test_today_the_llm_picks_the_closing_tag() -> None:
    assert CIERRE.rule(Abandono(transcript=TRANSCRIPT)) == ""


def test_jev_reads_how_the_abandoned_conversation_ended() -> None:
    state, [question] = CIERRE.ask(Abandono(transcript=TRANSCRIPT, purchase_confirmed=False, order_registered=False))
    inp = Abandono(transcript=TRANSCRIPT)

    assert question.kind == "choice" and set(question.options) == {
        "confirmado_sin_datos", "interesado", "rechazo", "compra_exitosa",
    }
    assert TRANSCRIPT in state and "confirmó la compra: no" in state
    assert CIERRE.decide(inp, _result(_tag("rechazo", 0.93)), "", {}) == "RECHAZO"
    assert CIERRE.decide(inp, _result(_tag("rechazo", 0.6)), "", {}) is None


def test_the_code_keeps_the_invariants_of_the_closing_tag() -> None:
    registered = Abandono(transcript=TRANSCRIPT, order_registered=True)
    unconfirmed = Abandono(transcript=TRANSCRIPT, purchase_confirmed=False)
    confirmed = Abandono(transcript=TRANSCRIPT, purchase_confirmed=True)

    assert CIERRE.floor(registered, "", "INTERESADO") == "COMPRA_EXITOSA"
    assert CIERRE.floor(unconfirmed, "", "CONFIRMADO_SIN_DATOS") == "INTERESADO"
    assert CIERRE.floor(confirmed, "", "CONFIRMADO_SIN_DATOS") == "CONFIRMADO_SIN_DATOS"
    assert CIERRE.floor(unconfirmed, "", "COMPRA_EXITOSA") == "", "sin pedido registrado no hay compra exitosa"
