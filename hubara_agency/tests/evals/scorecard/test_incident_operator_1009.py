"""Incidente del 2026-10-09 (···9824, ep_001): una venta bien atendida que Calidad
LLM marcó ALERTA en el turno 1 y FALLA en el turno 2.

* t1 · APE-01: el cliente contestó el seguimiento de una asesora («te escribe
  Liliana, asesora de Hubara…») y el bot saludó por la hora sin repetir la
  marca. Ya había un saludo nuestro con la marca: no se le cobra (operador).
* t2 · DES-05: «¿Tienen pago contra entrega?» → «aplica para compras desde
  $45.000 en productos… ¿Te muestro la trilogía del terror?». El monto es el
  mínimo del contra entrega y la trilogía la había mandado la asesora con su
  enlace: ni precio ni producto salieron de la memoria del bot.

La traza, reducida a lo que leen los checks; la calificación, la de producción
(`score_episode_turns`, modo turno).
"""
from __future__ import annotations

from src.plugins.chats.agent.sales_eval.scorecard import service
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from tests.evals.scorecard.dsl import T, tool, traj

_CTX = CheckContext(
    aromas=("Frutos rojos",),
    colors=("Blanco",),
    product_titles=("Trilogía del Terror", "Calabaza", "Momia", "Fantasma"),
    catalog_available=True,
    catalog_prices=(45000, 16000, 17500, 19000),
)
_LILIANA = (
    "[El cliente responde a este mensaje que le enviamos: «Hola, te escribe Liliana, asesora de Hubara, para hacer "
    "seguimiento a tu consulta de velas para este hallowen, tenemos la trilogia del terror: "
    "https://hubara.com.co/products/trilogia-del-terror/ o cada una por sepado... cuentame, en cual estas interesada?. "
    "Quedo atenta a tu respuesta.»]\nHola"
)


def _episode():
    return traj(
        T(1, inbound=_LILIANA, sent=[
            "Buenas tardes 🤍 Con gusto te ayudo con las velas de Halloween.\n\n"
            "¿Te interesa la trilogía completa o alguna en particular?",
        ]),
        T(2, inbound="Tienes pago contra entrega", sent=[
            "Perfecto, el contra entrega aplica para compras desde $45.000 en productos; el valor del envío lo "
            "confirma la transportadora al despachar.\n\n¿Te muestro la trilogía del terror para que la veas?",
        ]),
        T(3, inbound="Por favor", tools=[
            tool("search_products", q="halloween"),
            tool("present_product_detail", handle="trilogia-del-terror"),
        ], sent=["Esta es la Trilogía del Terror, con las tres piezas juntas y aroma a frutos rojos.\n\n¿Te la llevas?"]),
        episode_id="ep_001",
    )


def _verdicts():
    record = service.score_episode_turns(_episode(), _CTX, states={})
    return {r["turn"]: (r["verdict"], [x["check_id"] for x in r["results"] if x["verdict"] == "falla"]) for r in record["by_turn"]}


def test_answering_our_follow_up_the_bot_does_not_have_to_repeat_the_brand() -> None:
    verdict, failing = _verdicts()[1]
    assert "APE-01" not in failing
    assert verdict == "PASA"


def test_the_cash_on_delivery_minimum_and_the_product_we_sent_are_not_invented() -> None:
    verdict, failing = _verdicts()[2]
    assert "DES-05" not in failing
    assert verdict == "PASA"


# Desde el caso del 2026-10-09 (colega que escribe desde el chat), el ingest
# cita lo que mandó un colega —su plantilla de seguimiento incluida— con su
# propia nota (`quote_team_exchange_in_turn`). La calificación la lee igual
# que la cita de una plantilla: el mensaje es nuestro.

def _episode_with_colleague_note():
    from src.plugins.chats.agent.sales.use_cases.episode_memory import quote_team_exchange_in_turn

    liliana = _LILIANA.split("«", 1)[1].rsplit("»]", 1)[0]
    first = traj(
        T(1, inbound=quote_team_exchange_in_turn([("colega", liliana)], "Hola"), sent=_episode().turns[0].sent_texts),
        *[T(t.turn, inbound=t.inbound_text, sent=t.sent_texts) for t in _episode().turns[1:2]],
        episode_id="ep_001",
    )
    return first


def test_answering_a_colleague_the_bot_does_not_have_to_repeat_the_brand() -> None:
    record = service.score_episode_turns(_episode_with_colleague_note(), _CTX, states={})
    verdicts = {r["turn"]: [x["check_id"] for x in r["results"] if x["verdict"] == "falla"] for r in record["by_turn"]}

    assert "APE-01" not in verdicts[1]
    assert "APE-03" not in verdicts[1]
    assert "DES-05" not in verdicts[2]
