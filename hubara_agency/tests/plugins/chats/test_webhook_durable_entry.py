"""El webhook deja cada mensaje del cliente en Temporal ANTES de responderle 200
a Meta (ADR 2026-10-15). Antes el 200 salía primero y el ingest corría en un
BackgroundTask de la API: un deploy o un OOM a mitad perdía el mensaje y Meta
ya no reintentaba."""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.plugins.chats.agent.sales.contracts import InboundMessageInput

_PHONE_ID = "100000000000001"
_ANA = "573001234567"
_BETO = "573000000001"


def _msg(wamid: str, from_number: str) -> dict[str, Any]:
    return {"id": wamid, "from": from_number, "timestamp": "1789992000", "type": "text", "text": {"body": f"hola {wamid}"}}


def _body(*messages: dict[str, Any], statuses: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"metadata": {"phone_number_id": _PHONE_ID}, "messages": list(messages)}
    if statuses:
        value["statuses"] = statuses
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": value}]}]}


class _Entry:
    def __init__(self, *, fail_on: str | None = None, already: set[str] | None = None) -> None:
        self.fail_on = fail_on
        self.already = already or set()
        self.accepted: list[InboundMessageInput] = []

    async def accept(self, inp: InboundMessageInput) -> bool:
        wamid = inp.message["message_id"]
        if wamid == self.fail_on:
            raise ConnectionError("temporal cloud no contesta")
        if wamid in self.already:
            return False
        self.accepted.append(inp)
        return True

    def received(self) -> list[tuple[str, str | None]]:
        return [(i.message["message_id"], i.after) for i in self.accepted]


class _Ledger:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def append(self, records: list[dict[str, Any]]) -> None:
        self.records.extend(records)

    def stages(self) -> list[tuple[str, str]]:
        return [(r["stage"], r["wa_message_id"]) for r in self.records if r.get("kind") == "message" and r["stage"] != "seen"]


class _Ingest:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def execute(self, parsed: Any, **_: Any) -> None:
        self.calls.append(parsed)


@pytest.fixture
def webhook(monkeypatch):
    from src.main import app
    from src.platform import config
    from src.plugins.chats.api import sales as api

    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", "")
    monkeypatch.setattr(config, "HUBARA_ENV", "dev")
    ledger, ingest = _Ledger(), _Ingest()
    monkeypatch.setattr(api, "build_inbound_ledger", lambda: ledger)
    monkeypatch.setattr(api, "build_ingest_use_case", lambda: ingest, raising=False)

    def post(body: dict[str, Any], entry: _Entry):
        monkeypatch.setattr(api, "build_inbound_entry", lambda: entry)
        return TestClient(app).post("/api/webhook", json=body)

    return post, ledger, ingest


def test_each_message_enters_temporal_in_meta_order_before_the_200(webhook) -> None:
    post, ledger, ingest = webhook
    entry = _Entry()

    r = post(_body(_msg("wamid.A", _ANA), _msg("wamid.B", _BETO)), entry)

    assert r.status_code == 200
    assert entry.received() == [("wamid.A", None), ("wamid.B", None)]
    assert ledger.stages() == [("queued", "wamid.A"), ("queued", "wamid.B")]
    assert ingest.calls == []  # la API ya no ingiere: lo hace el worker


def test_a_customers_next_message_in_the_same_post_waits_for_the_previous(webhook) -> None:
    post, _, _ = webhook
    entry = _Entry()

    post(_body(_msg("wamid.A1", _ANA), _msg("wamid.B1", _BETO), _msg("wamid.A2", _ANA)), entry)

    assert entry.received() == [("wamid.A1", None), ("wamid.B1", None), ("wamid.A2", "wamid.A1")]


def test_the_message_travels_parsed_and_with_its_arrival_time(webhook) -> None:
    post, _, _ = webhook
    entry = _Entry()

    post(_body(_msg("wamid.A", _ANA)), entry)

    [inp] = entry.accepted
    assert (inp.message["from_number"], inp.message["text"], inp.message["phone_number_id"]) == (_ANA, "hola wamid.A", _PHONE_ID)
    assert inp.received_at_ms > 0


def test_when_temporal_fails_meta_gets_a_503_and_retries_the_post(webhook) -> None:
    post, ledger, _ = webhook
    entry = _Entry(fail_on="wamid.B")

    r = post(_body(_msg("wamid.A", _ANA), _msg("wamid.B", _BETO), _msg("wamid.C", _BETO)), entry)

    assert r.status_code == 503
    assert entry.received() == [("wamid.A", None)]
    assert ledger.stages() == [("queued", "wamid.A"), ("enqueue_failed", "wamid.B")]
    failed = [r for r in ledger.records if r.get("stage") == "enqueue_failed"]
    assert "ConnectionError" in failed[0]["error"]


def test_a_message_meta_delivers_again_gets_its_200_and_is_not_ingested_twice(webhook) -> None:
    post, ledger, _ = webhook
    entry = _Entry(already={"wamid.A"})

    r = post(_body(_msg("wamid.A", _ANA)), entry)

    assert r.status_code == 200
    assert entry.accepted == []
    assert ledger.stages() == [("duplicate", "wamid.A")]


def test_a_post_with_only_statuses_does_not_touch_temporal(webhook, monkeypatch) -> None:
    from src.plugins.chats.api import sales as api

    class _Delivery:
        async def execute(self, *_: Any, **__: Any) -> None:
            return None

    monkeypatch.setattr(api, "build_ingest_delivery_status_use_case", lambda: _Delivery())
    post, _, _ = webhook
    entry = _Entry()

    r = post(_body(statuses=[{"id": "wamid.OUT", "status": "delivered", "timestamp": "1789992000", "recipient_id": _ANA}]), entry)

    assert r.status_code == 200
    assert entry.accepted == []
