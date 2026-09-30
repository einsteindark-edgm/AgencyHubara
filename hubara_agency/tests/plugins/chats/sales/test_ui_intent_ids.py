"""Cada UI intent encolado lleva SU id.

El id permite un flush ACOTADO: una acción del operador desde la app móvil
manda solo lo que ella encoló, nunca otros intents que estén en la cola (p. ej.
las instrucciones de pago que "Crear pedido" encoló sin enviar, a propósito).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.tools.ui_intents import SendShippingRatesTool

_SID = "wa_test_ids"


@pytest.mark.asyncio
async def test_every_queued_ui_intent_gets_its_own_id(_isolate_vault_dir: Path) -> None:
    ctx = ToolContext(session_key=_SID, channel="whatsapp", chat_id=_SID)
    tool = SendShippingRatesTool(workspace=str(_isolate_vault_dir))

    await tool.execute_with_context(ctx)
    await tool.execute_with_context(ctx)

    data = json.loads((_isolate_vault_dir / _SID / "metadata.json").read_text(encoding="utf-8"))
    ids = [intent.get("id") for intent in data["pending_ui_intents"]]
    assert all(isinstance(i, str) and i for i in ids), ids
    assert len(set(ids)) == 2
