"""El flush de `pending_ui_intents` es invocable FUERA de un activity de Temporal.

D1.2b (2026-09-07): con Meta Business Agent al frente no hay turno del bot que
flushee los intents encolados por `register_order` (las instrucciones de pago).
El endpoint `session-actions@v1 /order` de chats llama la función plana; la
activity del workflow sigue existiendo y delega en ella. Guard: la función
plana envía y limpia el intent sin contexto de activity (sin `activity.info()`,
sin heartbeat), y la activity sigue registrada con el mismo nombre.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.plugins.chats.agent.sales.activities import flush_ui_intents

_SESSION = "wa_573001234567"


def _seed(vault, intents: list[dict]) -> None:
    (vault / _SESSION).mkdir(parents=True)
    (vault / _SESSION / "metadata.json").write_text(
        json.dumps({"phone_number_id": "pnid-1", "pending_ui_intents": intents}), encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_plain_flush_sends_payment_instructions_without_activity_context(tmp_path, monkeypatch):
    from src.platform import config
    from src.platform.whatsapp import client as wa_client

    monkeypatch.setattr(config, "WORKSPACE_VAULT_DIR", tmp_path)
    monkeypatch.setenv("PAYMENT_NEQUI_NUMBER", "3229041190")
    send_text = AsyncMock(return_value=SimpleNamespace(ok=True, wa_message_id="wamid.1", error=None))
    monkeypatch.setattr(wa_client, "send_text", send_text)
    _seed(tmp_path, [{
        "id": "payinstr-ord_1", "kind": "payment_instructions", "queued_at_ms": 4_000_000_000_000,
        "params": {"order_id": "ord_1", "subtotal_cop": 35000, "shipping_cop": 7900, "total_cop": 42900,
                   "currency": "COP", "method": "transfer"},
    }])

    sent = await flush_ui_intents.flush_pending_ui_intents(_SESSION)

    assert sent == 1
    send_text.assert_awaited_once()
    assert send_text.await_args.args[:2] == ("pnid-1", "573001234567")
    assert "3229041190" in send_text.await_args.args[2]
    data = json.loads((tmp_path / _SESSION / "metadata.json").read_text(encoding="utf-8"))
    assert data["pending_ui_intents"] == []


@pytest.mark.asyncio
async def test_activity_keeps_its_name_and_delegates_to_the_plain_function(monkeypatch):
    from temporalio import activity

    called: list[str] = []

    async def fake(session_id: str) -> int:
        called.append(session_id)
        return 7

    monkeypatch.setattr(flush_ui_intents, "flush_pending_ui_intents", fake)
    defn = activity._Definition.from_callable(flush_ui_intents.flush_pending_ui_intents_activity)
    assert defn is not None and defn.name == "flush_pending_ui_intents_activity"
    # sin contexto de activity el heartbeat no puede correr: la activity se
    # ejercita vía su función interna (`__wrapped__` del decorador de heartbeat)
    inner = flush_ui_intents.flush_pending_ui_intents_activity.__wrapped__
    assert await inner(_SESSION) == 7 and called == [_SESSION]
