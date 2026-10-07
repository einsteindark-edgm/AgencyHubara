"""Prueba del operador 2026-10-07 con el bot nuevo (ventas-3, ep_012): buena
conversación con dos ALERTA falsas en Calidad LLM.

* t4 · DES-08: el bot preguntó «¿Cuál de las cuatro te gustó? Dime el nombre
  y te cuento los detalles» y el cliente contestó «La de la calabaza». Dijo
  cuál le gustó, no que la quiera; la eligió en t7 y ahí quedó en el pedido.
* t9 · ENV-02: a «Si» + «Cuánto cuesta el envío ?» el bot mandó las tarifas y
  después el formulario. Respondió las dos cosas.

La traza, reducida a lo que leen los checks; la calificación, la de producción
(`score_episode_turns`, modo turno).
"""
from __future__ import annotations

from dataclasses import replace

from src.plugins.chats.agent.sales_eval.scorecard import service
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from src.plugins.chats.agent.sales_eval.scorecard.trajectory import InboundMsg
from tests.evals.scorecard.dsl import T, tool, traj

_CTX = CheckContext(
    aromas=("Frutos rojos",),
    colors=("Naranja",),
    product_titles=("Trilogía del Terror", "Calabaza", "Momia", "Fantasma"),
    catalog_available=True,
    catalog_prices=(49500, 16000, 17500, 19000),
)
_RATES = (
    "Nuestras tarifas mínimas de envío son 🚚:\n• Bogotá y municipios cercanos: $7.900\n"
    "• Nivel Nacional: $16.940\nEl valor definitivo se confirma al despachar según el tamaño y peso de tu paquete📏📦📦"
)
_FORM = (
    "Para enviarte tu pedido necesito unos datos 🤍\n\n• *1× Calabaza*\nSubtotal en productos: $16.000\n\n"
    "El envío va aparte (lo calcula la transportadora). Toca «Completar datos» para llenar el formulario "
    "(toma 30 segundos)."
)


def _burst(turn, *texts: str):
    return replace(turn, inbound=tuple(
        InboundMsg(seq=i + 1, ts_ms=turn.at_ms + i * 1000, kind="text", text=t) for i, t in enumerate(texts)
    ))


def _episode():
    return traj(
        T(2, inbound="Que tienen para Halloween ?", tools=[
            tool("search_products", q="Halloween"),
            tool("present_products", handles='["calabaza", "momia", "fantasma", "trilogia-del-terror"]',
                 intro_text="Claro que sí, esta es nuestra línea de Halloween 🎃 Todas vienen con aroma a frutos rojos."),
        ]),
        T(3, inbound="Me gusta esta", sent=[
            "Buena elección 🎃 ¿Cuál de las cuatro te gustó? Dime el nombre y te cuento los detalles.",
        ]),
        T(4, inbound="La de la calabaza", tools=[
            tool("get_product_by_handle", handle="calabaza"),
            tool("present_product_detail", handle="calabaza"),
        ], sent=[
            "Es una de las más lindas de la temporada: la calabaza esculpida con su sombrero de bruja, en aroma a "
            "frutos rojos.\n\n¿Te la llevas? Si quieres, te cuento cómo queda en un centro de mesa.",
        ]),
        T(6, inbound="Háblame más de la trilogía", tools=[
            tool("get_product_by_handle", handle="trilogia-del-terror"),
            tool("present_product_detail", handle="trilogia-del-terror"),
        ], sent=["Es un set de tres esculturas hechas a mano.\n\n¿Te animas con el set o prefieres solo la calabaza?"]),
        _burst(
            T(7, inbound="Que bonita\nPero\nMejor\nLe gusta\nLa calabaza", stage_out="variantes",
              tools=[tool("set_order_slot", producto="Calabaza")], draft={"producto": "Calabaza"},
              sent=["Perfecto, la Calabaza entonces 🎃\n\n¿Cuántas unidades quieres?"]),
            "Que bonita", "Pero", "Mejor", "Le gusta", "La calabaza",
        ),
        T(8, inbound="1", stage_in="variantes", tools=[tool("set_order_slot", cantidad="1", producto="Calabaza")],
          draft={"producto": "Calabaza", "cantidad": "1"},
          sent=["Listo, una Calabaza.\n\n¿La dejamos así y te pido los datos de envío?"]),
        _burst(
            T(9, inbound="Si\nCuánto cuesta el envío ?", stage_in="variantes", tools=[
                tool("send_shipping_rates", card_text=_RATES),
                tool("request_shipping_details", card_text=_FORM, items='[{"handle": "calabaza", "quantity": 1}]'),
            # El «Si» lo marcó el ingest (`confirmed`); la señal del turno es la
            # del último mensaje, la pregunta.
            ], draft={"producto": "Calabaza", "cantidad": "1"}, confirmed=True),
            "Si", "Cuánto cuesta el envío ?",
        ),
        episode_id="ep_012",
    )


def _failures(record, turn: int) -> list[str]:
    row = next(r for r in record["by_turn"] if r["turn"] == turn)
    return [r["check_id"] for r in row["results"] if r["verdict"] == "falla" and r["level"] != "menor"]


def test_liking_the_pumpkin_is_not_choosing_it() -> None:
    record = service.score_episode_turns(_episode(), _CTX, states={})

    assert "DES-08" not in _failures(record, 4)


def test_the_rates_and_the_form_answer_yes_and_how_much_is_shipping() -> None:
    record = service.score_episode_turns(_episode(), _CTX, states={})

    assert "ENV-02" not in _failures(record, 9)


def test_the_two_turns_are_no_longer_alerts() -> None:
    record = service.score_episode_turns(_episode(), _CTX, states={})
    verdicts = {r["turn"]: r["verdict"] for r in record["by_turn"]}

    assert (verdicts[4], verdicts[9]) == ("PASA", "PASA")
