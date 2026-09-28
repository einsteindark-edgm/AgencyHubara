"""Capacidades sobre el TEXTO del LLM (diseño v2 §07, familia B; fase F5),
del lado de las tools y activities que ya graban su resultado.

Cada una con la regla de hoy de respaldo (`reglas`, así nace todo) y una
pregunta cerrada a Jev (`sombra` / `jev`):

* persona — «¿Deja ver que quien atiende es un bot o una IA?», oración por
  oración, en las tools de cierre y de escalación. La autoidentificación y el
  anuncio del relevo «a una persona» son PISO (nunca se quedan); Jev puede
  dejar una frase de marca que la regla bota («Cada vela lleva un toque
  humano»).
"""
from __future__ import annotations

from src.plugins.chats.agent.sales.decisions.capabilities.texto import PERSONA, Frases
from src.sdk.connectorkit import PerceptionResult, TypedAnswer
from src.sdk.textkit import breaks_human_persona, customer_sentences

TH: dict[str, float] = {}


def _noul(qid: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="noul", p=p)


def _result(*answers: TypedAnswer) -> PerceptionResult:
    return PerceptionResult(ok=True, answers=answers, provider="fake", model="typesafe/jev-1.13-20260917")


def _frases(text: str) -> Frases:
    return Frases(parts=tuple(customer_sentences(text)))


BRAND_AND_RELAY = "Cada vela lleva un toque humano. Un humano te confirma el pago. Gracias por elegirnos 🤍"


def test_persona_rule_is_todays_regex_sentence_by_sentence() -> None:
    inp = _frases(BRAND_AND_RELAY)

    assert PERSONA.rule(inp) == tuple(i for i, p in enumerate(inp.parts) if breaks_human_persona(p)) == (0, 1)


def test_persona_asks_jev_one_closed_question_per_sentence() -> None:
    state, questions = PERSONA.ask(_frases(BRAND_AND_RELAY))

    assert [q.id for q in questions] == ["persona.1", "persona.2", "persona.3"]
    assert all(q.kind == "noul" for q in questions)
    assert "[1] Cada vela lleva un toque humano." in state and "[3] Gracias por elegirnos 🤍" in state


def test_jev_keeps_a_brand_sentence_the_rule_would_drop() -> None:
    inp = _frases(BRAND_AND_RELAY)
    result = _result(_noul("persona.1", 0.04), _noul("persona.2", 0.96), _noul("persona.3", 0.01))

    assert PERSONA.decide(inp, result, PERSONA.rule(inp), TH) == (1,)


def test_a_doubtful_sentence_keeps_the_rule_for_that_sentence() -> None:
    inp = _frases(BRAND_AND_RELAY)
    result = _result(_noul("persona.1", 0.5), _noul("persona.2", 0.96), _noul("persona.3", 0.01))

    assert PERSONA.decide(inp, result, PERSONA.rule(inp), TH) == (0, 1)


def test_without_answers_jev_does_not_decide() -> None:
    inp = _frases(BRAND_AND_RELAY)

    assert PERSONA.decide(inp, _result(), PERSONA.rule(inp), TH) is None


def test_self_identification_and_the_relay_to_a_person_are_a_floor() -> None:
    inp = _frases("Soy un asistente virtual de la tienda. Ya te paso con una persona del equipo. Gracias 🤍")
    jev_keeps_all: tuple[int, ...] = ()

    assert PERSONA.floor(inp, PERSONA.rule(inp), jev_keeps_all) == (0, 1)


def test_a_very_long_text_is_left_to_the_rule() -> None:
    long_text = " ".join(f"Oración número {i} del mensaje." for i in range(40))

    assert PERSONA.ask(_frases(long_text)) is None


def test_the_floor_never_drops_what_todays_rule_keeps() -> None:
    """El piso es un subconjunto de la regla: con Jev nunca se cae una
    oración que hoy pasa."""
    inp = _frases("Soy tu asistente de compras. Te ayudo con gusto 🤍")

    assert PERSONA.rule(inp) == ()
    assert PERSONA.floor(inp, PERSONA.rule(inp), ()) == ()


# ── enumeración: «¿Qué le enumera el texto al cliente?» ──
#
# Si el LLM lista 4+ aromas o colores en texto, la activity encola el selector
# de variantes (armado con el catálogo) y suprime el texto. Jev decide QUÉ
# enumera el texto (aromas, colores, combinaciones de cupón, productos o
# nada); las etiquetas las sigue sacando el código del catálogo. Las
# combinaciones «Color · Aroma» de un cupón NUNCA se vuelven selector (piso:
# prueba en vivo 2026-09-24, el selector destrozó el mensaje).

AROMAS = ("Lavanda", "Vainilla", "Canela", "Limoncillo", "Caballero de la noche")
COLORES = ("Rojo", "Blanco", "Negro", "Dorado")
LISTA = "Tenemos estos aromas: Lavanda, Vainilla, Canela y Limoncillo. ¿Cuál te gusta?"


def _texto(text: str):
    from src.plugins.chats.agent.sales.decisions.capabilities.texto import TextoCatalogo

    return TextoCatalogo(text=text, aromas=AROMAS, colors=COLORES)


def _choice(qid: str, choice: str, p: float) -> TypedAnswer:
    return TypedAnswer(id=qid, kind="choice", choice=choice, probs=((choice, p),), confidence=p)


def test_enumeracion_rule_is_todays_detector() -> None:
    from src.plugins.chats.agent.sales.decisions.capabilities.texto import ENUMERACION

    assert ENUMERACION.rule(_texto(LISTA)) == ("scent", ("Lavanda", "Vainilla", "Canela", "Limoncillo"))
    assert ENUMERACION.rule(_texto("Te recomiendo la de Lavanda 🤍")) == ()


def test_enumeracion_asks_only_when_the_text_names_enough_labels() -> None:
    from src.plugins.chats.agent.sales.decisions.capabilities.texto import ENUMERACION

    assert ENUMERACION.ask(_texto("Te recomiendo la de Lavanda 🤍")) is None
    state, [question] = ENUMERACION.ask(_texto(LISTA))
    assert question.kind == "choice" and LISTA in state
    assert set(question.options) == {"aromas", "colores", "combinaciones_cupon", "productos", "nada"}


def test_jev_confirms_the_picker_and_its_type() -> None:
    from src.plugins.chats.agent.sales.decisions.capabilities.texto import ENUMERACION

    inp = _texto(LISTA)
    decided = ENUMERACION.decide(inp, _result(_choice("enumeracion.que", "aromas", 0.95)), ENUMERACION.rule(inp), TH)

    assert decided == ("scent", ("Lavanda", "Vainilla", "Canela", "Limoncillo"))


def test_jev_can_say_the_labels_describe_one_product_not_a_list_to_choose() -> None:
    from src.plugins.chats.agent.sales.decisions.capabilities.texto import ENUMERACION

    inp = _texto("El Cubo Love mezcla Lavanda, Vainilla, Canela y Limoncillo en una sola vela.")
    decided = ENUMERACION.decide(inp, _result(_choice("enumeracion.que", "productos", 0.92)), ENUMERACION.rule(inp), TH)

    assert decided == ()


def test_a_doubtful_enumeration_is_left_to_the_rule() -> None:
    from src.plugins.chats.agent.sales.decisions.capabilities.texto import ENUMERACION

    inp = _texto(LISTA)

    assert ENUMERACION.decide(inp, _result(_choice("enumeracion.que", "nada", 0.55)), ENUMERACION.rule(inp), TH) is None


def test_coupon_combinations_never_become_a_picker() -> None:
    from src.plugins.chats.agent.sales.decisions.capabilities.texto import ENUMERACION

    inp = _texto("Con AMOR26 aplica en: Rojo · Lavanda, Blanco · Vainilla, Negro · Canela y Dorado · Limoncillo.")
    jev = ("scent", ("Lavanda", "Vainilla", "Canela", "Limoncillo"))

    assert ENUMERACION.rule(inp) == ()
    assert ENUMERACION.floor(inp, ENUMERACION.rule(inp), jev) == ()
