"""El webhook público deja rastro DURABLE de cada POST y de cada mensaje.

Auditoría 2026-09-18: con los logs del container perdidos en un deploy no se
pudo saber si un inbound "que Meta contó" entró o no. Contrato: todo mensaje
del body crudo queda `seen` ANTES de rutear; el ingest deja `ingested` o
`ingest_failed`; un POST rechazado deja su registro con el motivo.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

_PHONE_ID = "100000000000001"


class _Ledger:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def append(self, records: list[dict[str, Any]]) -> None:
        self.records.extend(records)

    def messages(self) -> list[tuple[str, str]]:
        return [(r["stage"], r["wa_message_id"]) for r in self.records if r["kind"] == "message"]

    def outcomes(self) -> list[str]:
        return [r["outcome"] for r in self.records if r["kind"] == "request"]


class _Ingest:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[Any] = []

    async def execute(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(args)
        if self.error is not None:
            raise self.error


def _change(wamid: str, from_number: str) -> dict[str, Any]:
    return {
        "field": "messages",
        "value": {
            "metadata": {"phone_number_id": _PHONE_ID},
            "messages": [{"id": wamid, "from": from_number, "timestamp": "1789992000", "type": "text", "text": {"body": "hola"}}],
        },
    }


def _body(*changes: dict[str, Any]) -> dict[str, Any]:
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": list(changes)}]}


@pytest.fixture
def harness(monkeypatch):
    from src.main import app
    from src.platform import config
    from src.plugins.chats.api import sales as api

    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", "")
    monkeypatch.setattr(config, "HUBARA_ENV", "dev")
    ledger = _Ledger()
    monkeypatch.setattr(api, "build_inbound_ledger", lambda: ledger, raising=False)

    def use(ingest: _Ingest) -> TestClient:
        monkeypatch.setattr(api, "build_ingest_use_case", lambda: ingest)
        return TestClient(app, raise_server_exceptions=False)

    return use, ledger


def test_an_inbound_message_is_seen_and_then_ingested(harness) -> None:
    use, ledger = harness
    r = use(_Ingest()).post("/api/webhook", json=_body(_change("wamid.A", "573001234567")))

    assert r.status_code == 200
    assert ledger.outcomes() == ["accepted"]
    assert ledger.messages() == [("seen", "wamid.A"), ("ingested", "wamid.A")]


def test_every_message_of_a_batched_post_is_seen_even_before_any_ingest(harness) -> None:
    use, ledger = harness
    use(_Ingest()).post("/api/webhook", json=_body(_change("wamid.A", "573001234567"), _change("wamid.B", "573000000001")))

    assert [m for m in ledger.messages() if m[0] == "seen"] == [("seen", "wamid.A"), ("seen", "wamid.B")]


def test_an_ingest_that_blows_up_leaves_ingest_failed_with_the_error(harness) -> None:
    use, ledger = harness
    use(_Ingest(error=RuntimeError("temporal unreachable"))).post("/api/webhook", json=_body(_change("wamid.A", "573001234567")))

    assert ledger.messages() == [("seen", "wamid.A"), ("ingest_failed", "wamid.A")]
    failed = ledger.records[-1]
    assert failed["error"] == "RuntimeError: temporal unreachable"
    assert failed["session_id"] == "wa_573001234567"


def test_a_rejected_signature_leaves_its_request_record(harness, monkeypatch) -> None:
    from src.platform import config

    use, ledger = harness
    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", "s3cret-real-value")
    r = use(_Ingest()).post("/api/webhook", json=_body(_change("wamid.A", "573001234567")), headers={"X-Hub-Signature-256": "sha256=bad"})

    assert r.status_code == 403
    assert ledger.outcomes() == ["signature_rejected"]
    assert ledger.messages() == []


# ── ledger × POST batcheado: rastro de TODO el batch ──────────────────────────

def test_every_message_of_a_batched_post_ends_up_ingested_in_the_ledger(harness) -> None:
    use, ledger = harness
    use(_Ingest()).post("/api/webhook", json=_body(_change("wamid.A", "573001234567"), _change("wamid.B", "573000000001")))

    assert ledger.messages() == [("seen", "wamid.A"), ("seen", "wamid.B"), ("ingested", "wamid.A"), ("ingested", "wamid.B")]


def test_a_failed_ingest_is_recorded_and_the_next_message_is_still_ingested(harness) -> None:
    use, ledger = harness

    class _FailsOnFirst(_Ingest):
        async def execute(self, *args: Any, **kwargs: Any) -> None:
            await super().execute(*args)
            if len(self.calls) == 1:
                raise RuntimeError("temporal unreachable")

    use(_FailsOnFirst()).post("/api/webhook", json=_body(_change("wamid.A", "573001234567"), _change("wamid.B", "573000000001")))

    assert ledger.messages() == [("seen", "wamid.A"), ("seen", "wamid.B"), ("ingest_failed", "wamid.A"), ("ingested", "wamid.B")]


def test_an_item_the_parser_rejects_is_recorded_with_its_reason(harness) -> None:
    """Sin esto un mensaje que el parser descarta quedaría `seen` sin desenlace
    (`lost`) y el motivo solo viviría en el log del container — que un deploy borra."""
    use, ledger = harness
    broken = _change("wamid.ROTO", "573001234567")
    broken["value"]["messages"][0]["text"] = {}

    r = use(_Ingest()).post("/api/webhook", json=_body(broken, _change("wamid.B", "573000000001")))

    assert r.status_code == 200
    assert ledger.messages() == [("seen", "wamid.ROTO"), ("seen", "wamid.B"), ("rejected", "wamid.ROTO"), ("ingested", "wamid.B")]
    rejected = next(r for r in ledger.records if r.get("stage") == "rejected")
    assert rejected["error"] == "text message missing 'text.body'"
    assert rejected["session_id"] == "wa_573001234567"
