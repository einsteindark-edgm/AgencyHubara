"""El flush de componentes devuelve qué entregó (traza v2, plan del laboratorio PR 2).

El modal del hilo muestra cada componente que salió al cliente (catálogo,
selector, botones) con su wamid, y los que fallaron. Antes la activity
devolvía solo la cantidad enviada. La función plana `flush_pending_ui_intents`
(la usa también el endpoint de pedidos) sigue devolviendo la cantidad.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.testing import ActivityEnvironment

from src.plugins.chats.agent.sales.activities import flush_ui_intents
from src.plugins.chats.agent.sales.activities.flush_ui_intents import flush_pending_ui_intents_activity
from tests.plugins.chats.sales.test_flush_intents_history_notes import (  # noqa: F401 — fixture
    _SESSION_ID,
    _seed_metadata,
    vault,
)

_QUICK_REPLIES = {
    "id": "i-1",
    "kind": "quick_replies",
    "params": {"body": "¿Te muestro el catálogo?", "buttons": [{"id": "b1", "title": "Sí"}]},
}
_UNKNOWN = {"id": "i-2", "kind": "no_existe", "params": {}}


@pytest.mark.asyncio
async def test_activity_reports_each_intent_with_its_wamid_and_outcome(vault) -> None:  # noqa: F811
    _seed_metadata(vault, [_QUICK_REPLIES, _UNKNOWN])

    report = await ActivityEnvironment().run(flush_pending_ui_intents_activity, _SESSION_ID)

    assert report == [
        {"kind": "quick_replies", "wamid": "wamid.test.1", "ok": True},
        {"kind": "no_existe", "wamid": None, "ok": False},
    ]


@pytest.mark.asyncio
async def test_meta_rejection_is_reported_as_not_delivered(vault, monkeypatch) -> None:  # noqa: F811
    import src.platform.whatsapp.client as wa_client

    monkeypatch.setattr(
        wa_client,
        "send_interactive_buttons",
        AsyncMock(return_value=SimpleNamespace(ok=False, wa_message_id=None, error="http_400")),
    )
    _seed_metadata(vault, [_QUICK_REPLIES])

    report = await ActivityEnvironment().run(flush_pending_ui_intents_activity, _SESSION_ID)

    assert report == [{"kind": "quick_replies", "wamid": None, "ok": False}]


@pytest.mark.asyncio
async def test_plain_function_still_returns_the_count(vault) -> None:  # noqa: F811
    _seed_metadata(vault, [_QUICK_REPLIES, _UNKNOWN])

    assert await flush_ui_intents.flush_pending_ui_intents(_SESSION_ID) == 1
