"""ENV-02 · trinquete del mensaje del formulario (revisión del PR #392).

El formulario de envío sale con un mensaje que arma el código y la traza lo
trae en `card_text`. Ese mensaje cuenta como respuesta del bot solo si, por
cada mensaje del cliente en el turno, al quitar lo que el formulario cubre (un
sí, una cantidad, el producto y las variantes que él mismo nombra, cortesías)
no queda nada. Con la versión anterior (interrogativas solo al principio y la
ráfaga aplanada) 19 de los 23 casos que deben fallar pasaban.

Casos de la sonda del revisor (`env02_probe.py`). Es un trinquete: se agregan
casos, nunca se sacan. Vive aparte de `test_checks_envio.py` porque el corpus
del modo turno (`test_focus_mode.py`) llama a las pruebas de familia sin
parámetros.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from src.plugins.chats.agent.sales_eval.scorecard.checks import CODE_CHECKS
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import InboundMsg, detect_signal
from tests.evals.scorecard.dsl import T, tool, traj
from tests.evals.scorecard.incidents import CATALOG_CTX

_FORM = "request_shipping_details"
_VARIANTS_DRAFT = {"producto": "Velón Koala", "color": "Blanco", "aroma": "Lavanda", "cantidad": "2"}

_NEEDS_A_BOT_TEXT = [
    "sí, cuánto se demora",
    "sí y cuánto vale el envío",
    "dale, a qué hora llega",
    "la de lavanda cuánto vale",
    "lavanda y cuánto es el envío",
    "lo tienen en azul",
    "y en azul",
    "quiero 2 cuánto sería",
    "sí y el envío",
    "ok y hacen envíos a pasto",
    "listo, envían a Cali",
    "2, me lo pueden enviar hoy",
    "Sí 👍 cuándo llega",
    "SÍ, CUÁNTO DEMORA",
    "Sí, de una. Y cuánto demora",
    "Si, aceptan tarjeta",
    "si, puedo pagar contra entrega",
    "y el envío?",
    "cuánto vale",
    "lo quiero pero en azul",
    "dale, me lo mandan hoy mismo",
    "si porfa, llega antes del viernes",
    "Lavanda, cuánto sale",
]
#: (mensaje del cliente, variantes que dice el formulario).
_FORM_ANSWERS = [
    ("sí, dale 👍", "Blanco, Lavanda"),
    ("👍", "Blanco, Lavanda"),
    ("2 por favor", "Blanco, Lavanda"),
    ("uno", "Blanco, Lavanda"),
    ("solo uno", "Blanco, Lavanda"),
    ("2 velas", "Blanco, Lavanda"),
    ("dos velones", "Blanco, Lavanda"),
    ("las dos", "Blanco, Lavanda"),
    ("Lavanda", "Blanco, Lavanda"),
    ("lavanda", "Blanco, Lavanda"),
    ("LAVANDA", "Blanco, Lavanda"),
    ("Sandalo", "Blanco, Sándalo"),
    ("blanco y lavanda", "Blanco, Lavanda"),
    ("x2", "Blanco, Lavanda"),
    ("2 de esas", "Blanco, Lavanda"),
    ("Siii", "Blanco, Lavanda"),
    ("Sii dale", "Blanco, Lavanda"),
    ("Bueno", "Blanco, Lavanda"),
    ("Perfecto gracias", "Blanco, Lavanda"),
    ("que sea en azul", "Azul, Lavanda"),
    ("Me encanta, lo quiero", "Blanco, Lavanda"),
    ("Hágale", "Blanco, Lavanda"),
    ("Listo 🙌", "Blanco, Lavanda"),
    ("❤️", "Blanco, Lavanda"),
    ("Ok", "Blanco, Lavanda"),
    ("De una", "Blanco, Lavanda"),
    ("Sí señor", "Blanco, Lavanda"),
    ("Claro que sí", "Blanco, Lavanda"),
    ("Sí, 2", "Blanco, Lavanda"),
    ("Dos por favor", "Blanco, Lavanda"),
    ("Las de lavanda", "Blanco, Lavanda"),
    ("2 blancas", "Blanco, Lavanda"),
    ("Una blanca y una azul", "1× Blanco, Lavanda; 1× Azul, Lavanda"),
    ("2 und", "Blanco, Lavanda"),
    ("2️⃣", "Blanco, Lavanda"),
    ("Sí, de una 🙏🏻", "Blanco, Lavanda"),
    ("sí, las 2 por favor 🙏", "Blanco, Lavanda"),
    ("Va", "Blanco, Lavanda"),
    ("Okis", "Blanco, Lavanda"),
]


def _form_message(variants: str = "Blanco, Lavanda") -> str:
    return (
        f"Para enviarte tu pedido necesito unos datos 🤍\n\n• *2× Velón Koala* ({variants})\n"
        "Subtotal en productos: $70.000\n\nEl envío va aparte (lo calcula la transportadora). "
        "Toca «Completar datos» para llenar el formulario (toma 30 segundos)."
    )


def _env02(inbound: str, *, variants: str = "Blanco, Lavanda", burst: list[str] | None = None,
           ctx: CheckContext | None = None):
    turn = T(1, inbound=inbound, signal=detect_signal(inbound), draft=_VARIANTS_DRAFT,
             tools=[tool(_FORM, card_text=_form_message(variants))])
    if burst:
        turn = replace(turn, inbound=tuple(
            InboundMsg(seq=i, ts_ms=i * 1000, kind="text", text=text) for i, text in enumerate(burst)
        ))
    return CODE_CHECKS["ENV-02"](traj(turn), CATALOG_CTX if ctx is None else ctx)


@pytest.mark.parametrize("inbound", _NEEDS_A_BOT_TEXT)
def test_env02_the_form_message_does_not_answer_a_question_or_a_condition(inbound: str) -> None:
    assert _env02(inbound).verdict == "falla", inbound


@pytest.mark.parametrize(("inbound", "variants"), _FORM_ANSWERS)
def test_env02_the_form_message_answers_a_short_yes_quantity_or_its_own_variant(inbound: str, variants: str) -> None:
    r = _env02(inbound, variants=variants)

    assert r.verdict == "pasa", (inbound, r.evidence)


@pytest.mark.parametrize(
    ("burst", "verdict"),
    [
        (["sí", "cuánto se demora"], "falla"),
        (["2", "y cuanto demora en llegar"], "falla"),
        (["Lavanda", "hacen envíos a Pasto"], "falla"),
        (["dale", "me llega mañana?"], "falla"),
        (["sí", "2"], "pasa"),
    ],
)
def test_env02_reads_every_message_of_the_burst(burst: list[str], verdict: str) -> None:
    assert _env02("\n".join(burst), burst=burst).verdict == verdict


@pytest.mark.parametrize("inbound", ["Azul", "Rosado", "Negro"])
def test_env02_a_variant_the_form_does_not_name_needs_a_reply(inbound: str) -> None:
    """El cliente eligió otra variante y el formulario salió con «(Blanco,
    Lavanda)»: el bot no recogió la elección; el formulario no le responde."""
    assert _env02(inbound).verdict == "falla"


@pytest.mark.parametrize("inbound", ["Lavanda", "Azul", "2 blancas"])
def test_env02_does_not_depend_on_the_catalog(inbound: str) -> None:
    """El veredicto sale solo de la traza: con o sin catálogo, el mismo."""
    assert _env02(inbound).verdict == _env02(inbound, ctx=CheckContext()).verdict
