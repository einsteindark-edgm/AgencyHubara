"""La espera del formulario solo la termina el formulario (premortem 2026-10-09).

Con el formulario de envío en curso, el workflow espera 10 min antes de dar
por abandonada la conversación (`shipping_flow_awaiting_reply_since_ms`,
sesión c4e3416f). Cualquier mensaje del cliente borraba esa marca: si
preguntaba algo («¿llega el martes?») y después tardaba en llenarlo, a los 5
min el cierre por abandono lo etiquetaba, la red escalaba al humano y el
formulario llegaba a una bandeja humana sin acuse. Ahora solo la respuesta
del formulario (`nfm_reply`) la borra; si nunca llega, la marca vence sola a
los 10 min (`read_idle_timeout_seconds`).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from tests.metadata_store_fakes import MergingMetadataStoreMixin

SID = "wa_573000000001"
FLAG = "shipping_flow_awaiting_reply_since_ms"


class _History:
    def append_user_event(self, session_id: str, content: str, **_: object) -> None:
        return None


class _Loader:
    async def execute(self, session_id: str, message: str, phone_number_id: str | None,
                      extra_context: list[str] | None = None, inbound_meta: dict | None = None) -> None:
        return None


class _Store(MergingMetadataStoreMixin):
    def __init__(self, initial: dict[str, dict[str, Any]]) -> None:
        self.store = dict(initial)

    def read(self, session_id: str) -> dict[str, Any]:
        return json.loads(json.dumps(self.store.get(session_id, {})))

    def write(self, session_id: str, data: dict[str, Any]) -> None:
        self.store[session_id] = json.loads(json.dumps(data))

    def update(self, session_id: str, mutator):
        fresh = self.read(session_id)
        result = mutator(fresh)
        if result is None:
            return None
        self.write(session_id, result)
        return result


def _store(flag_ms: int) -> _Store:
    episode = {"episode_id": "ep_001", "started_at_ms": flag_ms - 60_000, "closed_at_ms": None}
    return _Store({SID: {"episodes": [episode], FLAG: flag_ms}})


async def _ingest(store: _Store, msg: WhatsAppMessage) -> None:
    from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage

    use_case = IngestInboundMessage(history_store=_History(), load_session=_Loader(), metadata_store=store)  # type: ignore[arg-type]
    await use_case.execute(msg)


@pytest.mark.asyncio
async def test_a_question_while_filling_the_form_keeps_the_wait(_isolate_vault_dir: Path) -> None:
    store = _store(1_789_406_000_000)

    await _ingest(store, WhatsAppMessage(message_id="wamid.q", from_number="573000000001", phone_number_id="PID",
                                         text="¿llega el martes?", media=None, timestamp="1789406554"))

    assert store.read(SID)[FLAG] == 1_789_406_000_000


@pytest.mark.asyncio
async def test_the_form_reply_ends_the_wait(_isolate_vault_dir: Path) -> None:
    store = _store(1_789_406_000_000)
    reply = {"type": "nfm_reply", "name": "flow", "body": "Sent",
             "response_json": {"city": "Bogotá", "address": "Calle 1 # 2-3"}}

    await _ingest(store, WhatsAppMessage(message_id="wamid.f", from_number="573000000001", phone_number_id="PID",
                                         text="", media=None, timestamp="1789406554", interactive=reply))

    assert FLAG not in store.read(SID)
