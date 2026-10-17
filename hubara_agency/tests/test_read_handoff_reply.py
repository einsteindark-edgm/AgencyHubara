"""`read_handoff_reply_activity`: lo que el cliente le escribió a remarketing,
leído del chat, para el turno de Ventas que sigue al traspaso.

Caso del 2026-10-16: Ventas arrancó solo con el resumen de remarketing (sin el
«Si» del cliente, sin lo que había dicho un colega y sin el gancho) y contestó
NO_MESSAGE; un colega tuvo que mandar el formulario a mano.
"""
from __future__ import annotations

import json

import pytest
from temporalio.testing import ActivityEnvironment

pytestmark = pytest.mark.asyncio

SID = "wa_traspaso_lectura"


@pytest.fixture
def vault(_isolate_vault_dir):
    """El vault aislado del conftest (también el que lee `composition`)."""
    return _isolate_vault_dir


def _write_chat(vault, events: list[dict]) -> None:
    log = vault / SID / "sessions" / f"{SID}.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")


_CHAT = [
    {"role": "user", "content": "¿Cada una cuesta eso?"},
    {"role": "assistant", "content": "Así es, cada una.", "tools_used": ["send_reply"]},
    {"role": "assistant", "sender": "human", "content": "El precio es por la caja de 12"},
    {"role": "assistant", "sender": "human", "content": "¿Te dejo el pedido listo?"},
    {"role": "assistant", "agent": "remarketing", "content": "Hola de nuevo, ¿te ayudo a cerrar el pedido?"},
    {"role": "user", "content": "Si", "wamid": "wamid.in9", "timestamp": "2026-10-16T21:09:01+00:00"},
]


async def test_the_reply_carries_what_the_customer_wrote_and_what_ventas_missed(vault) -> None:
    _write_chat(vault, _CHAT)
    from src.plugins.chats.agent.sales.activities import read_handoff_reply_activity

    reply = await ActivityEnvironment().run(read_handoff_reply_activity, SID, [])

    assert reply is not None
    assert reply.customer_text == "Si"
    assert reply.text.endswith("\nSi")
    assert "«El precio es por la caja de 12»" in reply.text
    assert "«Hola de nuevo, ¿te ayudo a cerrar el pedido?»" in reply.text
    assert reply.wamids == ["wamid.in9"]


async def test_a_message_already_signaled_to_ventas_is_left_out(vault) -> None:
    _write_chat(vault, _CHAT)
    from src.plugins.chats.agent.sales.activities import read_handoff_reply_activity

    assert await ActivityEnvironment().run(read_handoff_reply_activity, SID, ["wamid.in9"]) is None


async def test_without_a_chat_there_is_no_reply(vault) -> None:
    from src.plugins.chats.agent.sales.activities import read_handoff_reply_activity

    assert await ActivityEnvironment().run(read_handoff_reply_activity, SID, []) is None
