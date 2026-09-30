"""Tests de `read_and_clear_pending_handoff_activity` (Fix 3).

Verifica:
  * Lee el handoff de metadata.json y lo retorna.
  * Limpia el field para que la siguiente llamada devuelva None (consumo
    atomico — no es idempotente, es read-once).
  * Si no hay handoff, retorna None sin escribir metadata.
"""
from __future__ import annotations

import json

import pytest
from temporalio.testing import ActivityEnvironment


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Redirige `WORKSPACE_VAULT_DIR` en bootstrap_session.py a un tmp."""
    monkeypatch.setattr(
        "src.plugins.chats.agent.sales.activities.bootstrap_session.WORKSPACE_VAULT_DIR",
        tmp_path,
    )
    return tmp_path


async def test_reads_and_clears_handoff_summary(vault) -> None:
    """Happy path: handoff escrito por dispatcher se consume y borra."""
    session_id = "wa_5491111111111"
    metadata_path = vault / session_id / "metadata.json"
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text(json.dumps({
        "pending_handoff_summary": "El cliente volvio a interactuar",
        "phone_number_id": "PID",
    }))

    from src.plugins.chats.agent.sales.activities import (
        read_and_clear_pending_handoff_activity,
    )
    env = ActivityEnvironment()
    summary = await env.run(read_and_clear_pending_handoff_activity, session_id)

    assert summary == "El cliente volvio a interactuar"

    # El field se borro pero el resto del metadata se preservo
    data_after = json.loads(metadata_path.read_text())
    assert "pending_handoff_summary" not in data_after
    assert data_after["phone_number_id"] == "PID"


async def test_returns_none_when_no_handoff_pending(vault) -> None:
    session_id = "wa_5492222222222"
    metadata_path = vault / session_id / "metadata.json"
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text(json.dumps({"phone_number_id": "PID"}))

    from src.plugins.chats.agent.sales.activities import (
        read_and_clear_pending_handoff_activity,
    )
    env = ActivityEnvironment()
    summary = await env.run(read_and_clear_pending_handoff_activity, session_id)

    assert summary is None


async def test_returns_none_when_metadata_missing(vault) -> None:
    """Sesion nueva sin metadata.json — read-only graceful."""
    from src.plugins.chats.agent.sales.activities import (
        read_and_clear_pending_handoff_activity,
    )
    env = ActivityEnvironment()
    summary = await env.run(
        read_and_clear_pending_handoff_activity, "wa_session_no_existente"
    )
    assert summary is None


async def test_consume_is_one_shot(vault) -> None:
    """Segunda llamada despues de consumir devuelve None — no esta cacheado."""
    session_id = "wa_5493333333333"
    metadata_path = vault / session_id / "metadata.json"
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text(json.dumps({"pending_handoff_summary": "ctx"}))

    from src.plugins.chats.agent.sales.activities import (
        read_and_clear_pending_handoff_activity,
    )
    env = ActivityEnvironment()
    first = await env.run(read_and_clear_pending_handoff_activity, session_id)
    second = await env.run(read_and_clear_pending_handoff_activity, session_id)

    assert first == "ctx"
    assert second is None


def _seed(vault, session_id: str, **metadata) -> None:
    path = vault / session_id / "metadata.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(metadata))


async def test_a_courtesy_reply_to_remarketing_reaches_sales_as_such(vault) -> None:
    """El cliente contestó el gancho con un «gracias» y el framing del
    traspaso pedía «si solo saludó, saluda breve y pregunta en qué puedes
    ayudar». Si la lectura `cortesia` marcó ESE mensaje, el resumen lo dice."""
    from src.plugins.chats.agent.sales.activities import read_and_clear_pending_handoff_activity

    _seed(vault, "wa_573001234567", pending_handoff_summary="Usuario respondió: Muchas gracias, muy amables",
          last_inbound_message_id="wamid.A", last_inbound_courtesy={"message_id": "wamid.A", "at_ms": 1})

    summary = await ActivityEnvironment().run(read_and_clear_pending_handoff_activity, "wa_573001234567")

    assert summary is not None and summary.startswith("Usuario respondió: Muchas gracias, muy amables")
    assert "solo agradece" in summary and "no preguntes en qué más puedes ayudar" in summary


async def test_a_courtesy_mark_of_an_older_message_does_not_count(vault) -> None:
    from src.plugins.chats.agent.sales.activities import read_and_clear_pending_handoff_activity

    _seed(vault, "wa_573001234567", pending_handoff_summary="Usuario respondió: ¿Y cuánto vale el envío?",
          last_inbound_message_id="wamid.NEW", last_inbound_courtesy={"message_id": "wamid.OLD", "at_ms": 1})

    summary = await ActivityEnvironment().run(read_and_clear_pending_handoff_activity, "wa_573001234567")

    assert summary == "Usuario respondió: ¿Y cuánto vale el envío?"
