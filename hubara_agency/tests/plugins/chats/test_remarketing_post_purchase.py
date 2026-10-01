"""Remarketing después de una compra: cerrar el ciclo, nunca vender
(simulación de la conversación real ···4148, 2026-10-01).

La clienta ya había comprado y recibido su pedido. Su gracias abrió un
episodio nuevo, sin borrador, y la instrucción interna del gancho la
describía como «El cliente miró productos pero NO eligió ninguno… vas a
re-abrir la conversación con UN único gancho». Decisión del operador:
  * ya compró y su último mensaje (una cortesía: agradece, felicita, confirma
    que le llegó) quedó sin respuesta → un cierre breve y cálido, sin ofrecer
    productos ni preguntar qué más quiere; Jev no lo corta;
  * ya compró y ya le respondimos → NO_MESSAGE, salvo que quedara algo
    abierto (una pregunta o un producto que pidió).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.remarketing.activities.context import read_remarketing_context_activity
from src.plugins.chats.agent.remarketing.prompts import build_remarketing_trigger
from src.plugins.chats.agent.remarketing.use_cases.context import context_from_metadata
from tests.plugins.chats.test_remarketing_ended_conversation import DELIVERED, SID, _ev, engine  # noqa: F401

BOUGHT = [
    {"episode_id": "ep_001", "closed_at_ms": 5, "closing_tag": "COMPRA_EXITOSA", "order_id": "order_X"},
    {"episode_id": "ep_002", "closed_at_ms": None, "msgs_count_at_start": 3},
]
UNANSWERED = [
    _ev("user", "Quiero el Velón Gorrión en lila"),
    _ev("assistant", "Listo, tu pedido quedó registrado 🤍"),
    _ev("assistant", DELIVERED),
    _ev("user", "Ya lo recibí. Muchas gracias"),
    _ev("user", "Te enviaremos fotos"),
]
ANSWERED = [*UNANSWERED, _ev("assistant", "Qué alegría 🤍 Quedamos atentos a esas fotos.")]


def test_a_buyer_whose_thanks_went_unanswered_is_owed_a_closing() -> None:
    assert context_from_metadata({"episodes": BOUGHT}, UNANSWERED).post_purchase == "closing"


def test_a_buyer_already_answered_is_not_written_again() -> None:
    assert context_from_metadata({"episodes": BOUGHT}, ANSWERED).post_purchase == "answered"


def test_without_a_purchase_before_nothing_changes() -> None:
    rejected = [{**BOUGHT[0], "closing_tag": "RECHAZO", "order_id": None}, BOUGHT[1]]

    assert context_from_metadata({"episodes": rejected}, UNANSWERED).post_purchase == ""
    assert context_from_metadata({"episodes": [BOUGHT[1]]}, UNANSWERED).post_purchase == ""


def test_the_closing_instruction_never_sells() -> None:
    trigger = build_remarketing_trigger("x", has_order_draft=False, post_purchase="closing")

    assert "YA COMPRÓ" in trigger and "quedó sin respuesta" in trigger
    assert "NO ofrezcas productos" in trigger and "NO preguntes qué más quiere" in trigger
    assert "miró productos pero NO eligió" not in trigger


def test_an_answered_buyer_gets_no_message_unless_something_is_open() -> None:
    trigger = build_remarketing_trigger("x", has_order_draft=False, post_purchase="answered")

    assert "YA COMPRÓ" in trigger and "NO_MESSAGE" in trigger and "quedó algo abierto" in trigger
    assert "miró productos pero NO eligió" not in trigger


def test_without_a_purchase_the_instruction_is_todays() -> None:
    assert "miró productos pero NO eligió" in build_remarketing_trigger("x", has_order_draft=False)


@pytest.mark.asyncio
async def test_jev_does_not_cut_a_closing_the_customer_is_owed(_isolate_vault_dir: Path, engine, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setenv("DECISIONS_BOT", "B")
    engine(sobra=0.79, terminada=0.92)
    session = _isolate_vault_dir / SID
    (session / "sessions").mkdir(parents=True)
    (session / "metadata.json").write_text(json.dumps({"tag": "RETOMA_VENTA", "episodes": BOUGHT}), encoding="utf-8")
    (session / "sessions" / f"{SID}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in UNANSWERED), encoding="utf-8")

    context = await ActivityEnvironment().run(read_remarketing_context_activity, SID)

    assert (context.post_purchase, context.skip_touch) == ("closing", False)
