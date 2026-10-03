"""Remarketing no le escribe a una conversación que ya terminó (operador,
2026-09-30).

Emula el caso: la conversación anterior terminó en una compra, el ETA avisó
la entrega y el cliente contestó «Ya lo recibí. Muchas gracias 💪». Ese
mensaje abrió un episodio nuevo, sin la etiqueta de la compra: la supresión
central por compra se levanta sola, y el gancho solo veía ese episodio.

Con el bot nuevo (Jev en `contactar`), la decisión ve cómo terminó la
conversación anterior y lo último que la tienda le escribió; Jev dice que la
conversación terminó y el toque se salta sin redactar nada. Las
probabilidades del Jev falso son las del Jev real sobre estos mismos casos
(sonda del 2026-09-30). Con el bot de hoy (`reglas`), Jev no se consulta:
decide el LLM, como siempre.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from src.platform.perception.adapters.fake import FakePerceptionAdapter
from src.plugins.chats.agent.remarketing.activities.context import read_remarketing_context_activity
from src.sdk.connectorkit import TypedAnswer

SID = "wa_573001234567"
DELIVERED = "¡Tu pedido #47 (Velón Gorrión) fue entregado! 🎉 Esperamos que lo disfrutes."


def _ev(role: str, content: str) -> dict:
    return {"role": role, "content": content, "timestamp": "2026-09-30T14:19:30+00:00"}


AFTER_DELIVERY = [
    _ev("user", "Quiero el Velón Gorrión en lila"),
    _ev("assistant", "Listo, tu pedido quedó registrado 🤍"),
    _ev("assistant", DELIVERED),
    _ev("user", "Ya lo recibí. Muchas gracias 💪"),
    _ev("assistant", "Qué alegría, que lo disfrutes mucho 🤍"),
]
OPEN_SALE = [
    _ev("user", "¿Cuánto vale el Velón Gorrión?"),
    _ev("assistant", "Vale $44.000. ¿En qué color lo quieres, lila o azul?"),
]


def _seed(vault: Path, events: list[dict], *, after_purchase: bool) -> None:
    episodes = [{"episode_id": "ep_001", "closed_at_ms": None, "msgs_count_at_start": 0}]
    if after_purchase:
        episodes = [
            {"episode_id": "ep_001", "closed_at_ms": 5, "closing_tag": "COMPRA_EXITOSA", "order_id": "order_X"},
            {"episode_id": "ep_002", "closed_at_ms": None, "msgs_count_at_start": 3},
        ]
    session = vault / SID
    (session / "sessions").mkdir(parents=True)
    (session / "metadata.json").write_text(json.dumps({"tag": "INTERESADO", "episodes": episodes}), encoding="utf-8")
    (session / "sessions" / f"{SID}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


@pytest.fixture
def engine(monkeypatch):
    """El decisor real de `contactar` conectado, con un Jev falso."""
    from src.plugins.chats.agent.sales.decisions.contact import register_contact_decision
    from src.plugins.chats.shared.agent_decisions import clear_contact_decider
    from src.sdk import connectorkit

    holder: dict = {}

    def jev(sobra: float, terminada: float) -> FakePerceptionAdapter:
        holder["fake"] = FakePerceptionAdapter({
            "contactar.sobra": TypedAnswer(id="contactar.sobra", kind="noul", p=sobra),
            "contactar.terminada": TypedAnswer(id="contactar.terminada", kind="noul", p=terminada),
        })
        return holder["fake"]

    monkeypatch.setattr(connectorkit, "get_perception_port", lambda _oracle: holder["fake"])
    register_contact_decision()
    yield jev
    clear_contact_decider()


@pytest.mark.asyncio
async def test_the_new_bot_skips_a_conversation_that_already_ended(_isolate_vault_dir: Path, engine, monkeypatch) -> None:
    monkeypatch.setenv("DECISIONS_BOT", "B")
    fake = engine(sobra=0.74, terminada=0.91)
    _seed(_isolate_vault_dir, AFTER_DELIVERY, after_purchase=True)

    context = await ActivityEnvironment().run(read_remarketing_context_activity, SID)

    assert context.skip_touch is True
    [(state, questions)] = fake.calls
    assert "terminó en una compra" in state and DELIVERED in state
    assert questions == ("contactar.sobra", "contactar.terminada")


@pytest.mark.asyncio
async def test_an_open_sale_still_gets_its_touch(_isolate_vault_dir: Path, engine, monkeypatch) -> None:
    monkeypatch.setenv("DECISIONS_BOT", "B")
    engine(sobra=0.2, terminada=0.05)
    _seed(_isolate_vault_dir, OPEN_SALE, after_purchase=False)

    context = await ActivityEnvironment().run(read_remarketing_context_activity, SID)

    assert context.skip_touch is False


@pytest.mark.asyncio
async def test_todays_bot_leaves_it_to_the_llm(_isolate_vault_dir: Path, engine, monkeypatch) -> None:
    monkeypatch.delenv("DECISIONS_BOT", raising=False)
    fake = engine(sobra=0.74, terminada=0.91)
    _seed(_isolate_vault_dir, AFTER_DELIVERY, after_purchase=True)

    context = await ActivityEnvironment().run(read_remarketing_context_activity, SID)

    assert context.skip_touch is False and fake.calls == []
