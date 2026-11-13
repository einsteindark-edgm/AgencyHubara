"""DES-03 mira solo lo que escribió el cliente (tarjeta 23 del ciclo).

El texto del turno trae notas que arma el ingest con mensajes NUESTROS. Acá se
arman con los mismos constructores de producción, para que un cambio de formato
de la nota se vea en esta prueba. Datos inventados.
"""
from __future__ import annotations

from src.plugins.chats.agent.sales.use_cases.campaign_reply import quote_campaign_in_turn
from src.plugins.chats.agent.sales.use_cases.episode_memory import quote_notices_in_turn
from src.plugins.chats.agent.sales_eval.scorecard.checks import CODE_CHECKS
from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext
from src.plugins.chats.shared.team_exchange import quote_team_exchange_in_turn
from tests.evals.scorecard.dsl import T, traj


def _des03(inbound: str):
    t = traj(T(1, inbound=inbound, sent=["¡Con gusto!"]))
    return CODE_CHECKS["DES-03"](t, CheckContext())


def test_catalog_word_only_in_a_store_notice_is_not_a_request() -> None:
    inbound = quote_notices_in_turn(["Tu pedido #900 fue entregado. Mira el catálogo cuando quieras."], "gracias")
    assert _des03(inbound).verdict == "no_aplica"


def test_catalog_word_only_in_the_campaign_we_sent_is_not_a_request() -> None:
    touch = {"campaign_name": "Fin de año", "message": "Llegó el catálogo de fin de año"}
    assert _des03(quote_campaign_in_turn(touch, "gracias")).verdict == "no_aplica"


def test_campaign_without_message_keeps_what_the_customer_wrote() -> None:
    inbound = quote_campaign_in_turn({"campaign_name": "Fin de año"}, "mándame el catálogo")
    assert _des03(inbound).verdict == "falla"


def test_request_the_customer_wrote_while_a_colleague_had_the_chat_still_counts() -> None:
    """Las líneas «Cliente» de la nota del colega sí las escribió el cliente y no
    fueron turno del bot en ningún otro lado: su pedido no se pierde."""
    exchange = [
        ("colega", "Hola, ya te atiendo"),
        ("cliente", "mándame el catálogo porfa"),
        ("aviso", "Tu pedido va en camino"),
    ]
    r = _des03(quote_team_exchange_in_turn(exchange, "¿hola?"))
    assert (r.verdict, r.turn) == ("falla", 1)


def test_colleague_mentioning_the_catalog_is_not_a_request() -> None:
    exchange = [("colega", "Te dejo el catálogo aquí"), ("cliente", "listo")]
    assert _des03(quote_team_exchange_in_turn(exchange, "gracias")).verdict == "no_aplica"


def test_request_written_as_the_caption_of_a_photo_still_counts() -> None:
    """La forma real del ingest: el texto va pegado a la nota de la foto."""
    inbound = '[el cliente envió una foto: una vela verde sobre una mesa] con el texto: "mándame el catálogo"'
    assert _des03(inbound).verdict == "falla"
