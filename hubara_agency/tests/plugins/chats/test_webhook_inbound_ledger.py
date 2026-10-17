"""El webhook público deja rastro DURABLE de cada POST y de cada mensaje.

Auditoría 2026-09-18: con los logs del container perdidos en un deploy no se
pudo saber si un inbound "que Meta contó" entró o no. Contrato: todo mensaje
del body crudo queda `seen` ANTES de rutear; el webhook deja `queued` cuando
el mensaje quedó en Temporal (entrada durable, ADR 2026-10-15) y la activity
del worker deja `ingested` o `ingest_failed` (`test_ingest_inbound_activity.py`);
un POST rechazado deja su registro con el motivo.
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


class _Entry:
    """La entrada a Temporal: acepta todo (su falla la cubre `test_webhook_durable_entry.py`)."""

    async def accept(self, inp: Any) -> bool:
        return True


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

    def use(entry: _Entry) -> TestClient:
        monkeypatch.setattr(api, "build_inbound_entry", lambda: entry)
        return TestClient(app, raise_server_exceptions=False)

    return use, ledger


def test_an_inbound_message_is_seen_and_then_queued_in_temporal(harness) -> None:
    use, ledger = harness
    r = use(_Entry()).post("/api/webhook", json=_body(_change("wamid.A", "573001234567")))

    assert r.status_code == 200
    assert ledger.outcomes() == ["accepted"]
    assert ledger.messages() == [("seen", "wamid.A"), ("queued", "wamid.A")]


def test_every_message_of_a_batched_post_is_seen_even_before_any_ingest(harness) -> None:
    use, ledger = harness
    use(_Entry()).post("/api/webhook", json=_body(_change("wamid.A", "573001234567"), _change("wamid.B", "573000000001")))

    assert [m for m in ledger.messages() if m[0] == "seen"] == [("seen", "wamid.A"), ("seen", "wamid.B")]


def test_a_rejected_signature_leaves_its_request_record(harness, monkeypatch) -> None:
    from src.platform import config

    use, ledger = harness
    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", "s3cret-real-value")
    r = use(_Entry()).post("/api/webhook", json=_body(_change("wamid.A", "573001234567")), headers={"X-Hub-Signature-256": "sha256=bad"})

    assert r.status_code == 403
    assert ledger.outcomes() == ["signature_rejected"]
    assert ledger.messages() == []


# ── ledger × POST batcheado: rastro de TODO el batch ──────────────────────────

def test_every_message_of_a_batched_post_ends_up_queued_in_the_ledger(harness) -> None:
    use, ledger = harness
    use(_Entry()).post("/api/webhook", json=_body(_change("wamid.A", "573001234567"), _change("wamid.B", "573000000001")))

    assert ledger.messages() == [("seen", "wamid.A"), ("seen", "wamid.B"), ("queued", "wamid.A"), ("queued", "wamid.B")]


def test_an_item_the_parser_rejects_is_recorded_with_its_reason(harness) -> None:
    """Sin esto un mensaje que el parser descarta quedaría `seen` sin desenlace
    (`lost`) y el motivo solo viviría en el log del container — que un deploy borra."""
    use, ledger = harness
    broken = _change("wamid.ROTO", "573001234567")
    broken["value"]["messages"][0]["text"] = {}

    r = use(_Entry()).post("/api/webhook", json=_body(broken, _change("wamid.B", "573000000001")))

    assert r.status_code == 200
    assert ledger.messages() == [("seen", "wamid.ROTO"), ("seen", "wamid.B"), ("rejected", "wamid.ROTO"), ("queued", "wamid.B")]
    rejected = next(r for r in ledger.records if r.get("stage") == "rejected")
    assert rejected["error"] == "text message missing 'text.body'"
    assert rejected["session_id"] == "wa_573001234567"


# ── ledger × clientes con nombre de usuario (sin teléfono, solo BSUID) ────────

def _username_change(wamid: str, user_id: str) -> dict[str, Any]:
    change = _change(wamid, "")
    del change["value"]["messages"][0]["from"]
    change["value"]["messages"][0]["from_user_id"] = user_id
    return change


def test_a_customer_without_phone_is_recorded_under_their_conversation(harness, monkeypatch, tmp_path) -> None:
    """Sin esto el ledger dejaba `session_id: null`: no se podía contar
    personas ni ubicar el chat (ledger 2026-09-25)."""
    from src.plugins.chats.agent.sales.sender_identity_store import FilesystemSenderIdentity
    from src.plugins.chats.api import sales as api

    monkeypatch.setattr(api, "build_sender_identity", lambda: FilesystemSenderIdentity(tmp_path))
    use, ledger = harness
    use(_Entry()).post("/api/webhook", json=_body(_username_change("wamid.U", "CO.9990000000000002")))

    seen, queued = [r for r in ledger.records if r["kind"] == "message"]
    assert (seen["session_id"], seen["user_id"]) == ("wa_CO9990000000000002", "CO.9990000000000002")
    assert queued["session_id"] == "wa_CO9990000000000002"


def test_a_rejected_meta_user_id_is_kept_in_the_ledger(harness) -> None:
    use, ledger = harness
    use(_Entry()).post("/api/webhook", json=_body(_username_change("wamid.X", "CO.ENT.1502")))

    rejected = next(r for r in ledger.records if r.get("stage") == "rejected")
    assert rejected["user_id"] == "CO.ENT.1502"
