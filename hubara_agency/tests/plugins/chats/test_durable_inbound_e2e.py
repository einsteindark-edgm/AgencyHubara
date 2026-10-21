"""La entrada durable de punta a punta (ADR 2026-10-15; gotcha #1: verificar
comportamiento, no solo el schema).

POST real al webhook → `InboundEntry` real contra un Temporal de prueba →
`InboundMessageWorkflow` y sus activities reales en un worker → el
`IngestInboundMessage` real (con historial, sesión y metadata falsos) → el bot
recibe el mensaje. Meta lo re-entrega: no llega dos veces. Nada de esto pasa por
un fake de la entrada ni del ingest: si el mensaje no sobrevive la
serialización de Temporal o el nombre de la cola no coincide, este test lo ve.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.chats.agent.sales.activities.inbound_entry import (
    ingest_inbound_message_activity,
    wait_inbound_predecessor_activity,
)
from src.plugins.chats.agent.sales.inbound_effects_store import FilesystemInboundEffects
from src.plugins.chats.agent.sales.use_cases.inbound_entry import InboundEntry
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage
from src.plugins.chats.agent.sales.workflows.inbound_message import InboundMessageWorkflow
from src.sdk import get_task_queue
from tests.metadata_store_fakes import MergingMetadataStoreMixin

_ANA = "573001234567"


class _History:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def append_user_event(self, session_id: str, content: str, **_: Any) -> None:
        self.events.append((session_id, content))


class _Session:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def execute(self, session_id: str, message: str, phone_number_id: str | None, **_: Any) -> None:
        self.calls.append((session_id, message))


class _Metadata(MergingMetadataStoreMixin):
    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    def read(self, session_id: str) -> dict:
        return dict(self.store.get(session_id, {}))

    def write(self, session_id: str, data: dict) -> None:
        self.store[session_id] = dict(data)

    def update(self, session_id: str, mutator):
        result = mutator(self.read(session_id))
        if result is not None:
            self.write(session_id, result)
        return result


class _Ledger:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def append(self, records: list[dict[str, Any]]) -> None:
        self.records.extend(records)

    def stages(self) -> list[str]:
        return [r["stage"] for r in self.records if r.get("kind") == "message"]


def _body(wamid: str, text: str) -> dict[str, Any]:
    value = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15550000000", "phone_number_id": "100000000000001"},
        "contacts": [{"profile": {"name": "Ana"}, "wa_id": _ANA}],
        "messages": [{"from": _ANA, "id": wamid, "timestamp": "1789992000", "type": "text", "text": {"body": text}}],
    }
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": value}]}]}


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        yield env


@pytest.mark.asyncio
async def test_a_message_posted_to_the_webhook_reaches_the_bot_through_temporal_once(env, monkeypatch, tmp_path) -> None:
    from src.main import app
    from src.platform import config
    from src.plugins.chats.agent.sales import composition
    from src.plugins.chats.api import sales as api

    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", "")
    monkeypatch.setattr(config, "HUBARA_ENV", "dev")
    ledger, history, session = _Ledger(), _History(), _Session()
    monkeypatch.setattr(api, "build_inbound_ledger", lambda: ledger)
    monkeypatch.setattr(composition, "build_inbound_ledger", lambda: ledger)
    ingest = IngestInboundMessage(
        history_store=history,  # type: ignore[arg-type]
        load_session=session,  # type: ignore[arg-type]
        metadata_store=_Metadata(),  # type: ignore[arg-type]
        effects=FilesystemInboundEffects(tmp_path / "effects", now_ms=lambda: 1_789_992_000_000),
    )
    monkeypatch.setattr(composition, "build_ingest_use_case", lambda: ingest)
    queue = get_task_queue("chats", "sales")

    async def client():
        return env.client

    monkeypatch.setattr(api, "build_inbound_entry", lambda: InboundEntry(client, task_queue=queue))
    body = _body("wamid.E2E", "hola, quiero velas")

    async with Worker(
        env.client,
        task_queue=queue,
        workflows=[InboundMessageWorkflow],
        activities=[ingest_inbound_message_activity, wait_inbound_predecessor_activity],
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://hubara") as http:
            first = await http.post("/api/webhook", json=body)
            assert await env.client.get_workflow_handle("inbound-wamid.E2E").result() == "ingested"
            again = await http.post("/api/webhook", json=body)  # Meta re-entrega

    assert (first.status_code, again.status_code) == (200, 200)
    assert session.calls == [(f"wa_{_ANA}", "hola, quiero velas")]
    assert history.events == [(f"wa_{_ANA}", "hola, quiero velas")]
    assert ledger.stages() == ["seen", "queued", "ingested", "seen", "duplicate"]
