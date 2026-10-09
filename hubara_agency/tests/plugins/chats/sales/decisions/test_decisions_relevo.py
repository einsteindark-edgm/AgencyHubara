"""«¿El texto le promete al cliente que una persona del equipo lo va a atender?»
(capacidad `relevo`).

Laboratorio caso-cortesia-1001 (r1 y r2, 2026-09-30), caso de control: el ETA
avisó «tu pedido ya está listo… ¿Nos confirmas para coordinar la entrega?»,
el cliente pidió que se lo llevaran hoy a la portería y los dos bots
contestaron «…un colega del equipo coordina contigo la entrega…» SIN llamar
`escalate_to_human`. La conversación siguió en la ruta del bot: nadie quedaba
avisado y el cliente esperaba a una persona. Producción, en el mismo caso,
escaló.

La red de seguridad (antes de enviar el texto final) escala cuando el texto
promete el relevo. La regla (las frases del relevo) es PISO; Jev suma las
paráfrasis que la regla no conoce.
"""
from __future__ import annotations

import pytest

from src.plugins.chats.agent.sales.decisions import bots
from src.plugins.chats.agent.sales.decisions.capabilities.texto import RELEVO, TextoAlCliente
from src.sdk.connectorkit import PerceptionResult, TypedAnswer

PROMISES = (
    "Buenas noches 🤍 Claro que sí, un colega del equipo coordina contigo la entrega de hoy después de las 5 en la portería.",
    "Claro que sí, tomo nota de la portería después de las 5.\n\nUn colega del equipo te confirma la entrega de hoy en este mismo chat 🤍",
    "Perfecto 🤍 Ya le paso el aviso a un colega del equipo para que coordine la entrega contigo en este mismo chat.",
    "Listo, nuestro equipo te va a escribir para cuadrar la hora.",
    "Te paso con una colega que maneja los pedidos grandes 🤍",
    # Laboratorio r3: el bot nuevo pone lo que se coordina entre el verbo y
    # «contigo», y «pasa el caso» en vez de «te paso con».
    "Claro que sí, un colega del equipo coordina la entrega contigo en este mismo chat para hoy después de las 5 🤍",
    "Déjame pasar tu caso con un colega del equipo para que confirme la logística contigo en este mismo chat.",
    "Ya pasamos tu solicitud a una asesora 🤍",
)
NOT_PROMISES = (
    "El equipo de Hubara hace cada vela a mano 🤍",
    "Qué alegría que ya lo tengas contigo 🤍 Cualquier cosa que necesites, aquí estamos.",
    "Te confirmo el pedido: 2 Velón Gorrión en lila.",
    "¿En qué color lo quieres, lila o azul?",
    "Te paso el enlace con un descuento para tu próxima compra 🤍",
    "Coordino la entrega contigo: ¿a qué hora te queda bien?",
    # Premortem 2026-10-09: un aviso que llega con un evento del pedido (lo
    # manda el sistema al despachar) no es pasarle la conversación a nadie;
    # leerlo como relevo escalaba y callaba al bot en plena venta.
    "Listo 🤍 Nuestro equipo te avisa cuando despachemos tu pedido.",
    "El equipo te confirma la guía cuando salga el envío 🚚",
    "Un colega te escribe cuando tu pedido esté listo para despachar.",
)


def _result(p: float | None) -> PerceptionResult:
    answers = () if p is None else (TypedAnswer(id="relevo.promete", kind="noul", p=p),)
    return PerceptionResult(ok=True, answers=answers, provider="fake", model="typesafe/jev-1.13-20260917")


@pytest.mark.parametrize("text", PROMISES)
def test_the_rule_knows_the_handoff_phrases(text: str) -> None:
    assert RELEVO.rule(TextoAlCliente(text=text)) is True


@pytest.mark.parametrize("text", NOT_PROMISES)
def test_the_rule_leaves_everything_else(text: str) -> None:
    assert RELEVO.rule(TextoAlCliente(text=text)) is False


def test_jev_reads_the_whole_message() -> None:
    asked = RELEVO.ask(TextoAlCliente(text=PROMISES[0]))

    assert asked is not None
    state, [question] = asked
    assert PROMISES[0] in state and question.id == "relevo.promete" and question.kind == "noul"
    assert RELEVO.ask(TextoAlCliente(text="  ")) is None


def test_jev_adds_what_the_rule_does_not_know_but_never_takes_a_promise_away() -> None:
    """«La persona de despachos te contacta para cuadrar» no está en la regla;
    una frase de la regla no la quita un «no» de Jev."""
    paraphrase = TextoAlCliente(text="Listo, lo dejo anotado y la persona de despachos te contacta para cuadrar.")
    promise = TextoAlCliente(text=PROMISES[0])

    assert RELEVO.decide(paraphrase, _result(0.93), False, {}) is True
    assert RELEVO.floor(paraphrase, False, True) is True
    assert RELEVO.floor(promise, True, False) is True
    assert RELEVO.decide(paraphrase, _result(0.5), False, {}) is None


def test_the_dashboard_control_knows_it() -> None:
    assert "relevo" in bots.CAPABILITIES
