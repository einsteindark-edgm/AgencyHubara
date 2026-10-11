"""El traspaso determinista de remarketing a ventas lleva el mensaje entero
(premortem 2026-10-09): `str(msg.message)[:60]` cortaba la dirección o el
pedido que el cliente mandó como respuesta a un toque, y ventas solo ve ese
resumen: pedía el dato otra vez o guardaba una dirección a medias. Los otros
caminos del traspaso ya llevaban 500 caracteres."""
from __future__ import annotations

from src.plugins.chats.agent.remarketing.workflows.remarketing import reply_summary


def test_a_long_reply_reaches_sales_whole() -> None:
    reply = "Sí, mándamela a la Calle 123 # 45-67, apto 101, torre 1, Barrio Ejemplo, Bogotá; recibe Ana Prueba"

    assert reply_summary(reply) == "Usuario respondió: " + reply


def test_the_summary_keeps_the_cap_of_the_other_handoffs() -> None:
    assert len(reply_summary("x" * 2000)) == len("Usuario respondió: ") + 500
