"""Ledger durable de inbound — qué queda escrito por cada POST al webhook.

Auditoría 2026-09-18 (campaña halloween): Meta contaba 7 conversaciones y el
vault 5; los logs del container se habían perdido en un deploy y no se pudo
saber si los 2 webhooks faltantes entraron o no. El ledger es la respuesta
durable a esa pregunta.
"""
from __future__ import annotations

from typing import Any

from src.plugins.chats.agent.sales.use_cases.inbound_ledger import webhook_ledger_records

_PHONE_ID = "100000000000001"


def _message(wamid: str, from_number: str, *, referral: dict[str, Any] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {
        "id": wamid,
        "from": from_number,
        "timestamp": "1789992000",
        "type": "text",
        "text": {"body": "Hola, quiero más información"},
    }
    if referral is not None:
        msg["referral"] = referral
    return msg


def _change(field: str, value: dict[str, Any]) -> dict[str, Any]:
    return {"field": field, "value": {"metadata": {"phone_number_id": _PHONE_ID}, **value}}


def _body(*changes: dict[str, Any]) -> dict[str, Any]:
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": list(changes)}]}


_AD_REFERRAL = {
    "source_type": "ad",
    "source_id": "120200000000000001",
    "headline": "Velas aromáticas",
    "ctwa_clid": "clid-sintetico-1",
}


def _messages(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in records if r["kind"] == "message"]


def _requests(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in records if r["kind"] == "request"]


def test_each_post_leaves_one_request_record_counting_messages_and_statuses() -> None:
    body = _body(
        _change("messages", {"messages": [_message("wamid.A", "573001234567")]}),
        _change("messages", {"statuses": [{"id": "wamid.OUT.1", "status": "delivered"}, {"id": "wamid.OUT.2", "status": "read"}]}),
    )

    records = webhook_ledger_records(body, at_ms=5)

    assert _requests(records) == [
        {"kind": "request", "at_ms": 5, "outcome": "accepted", "fields": ["messages"], "n_messages": 1, "n_statuses": 2}
    ]


def test_a_rejected_post_still_leaves_its_request_record() -> None:
    records = webhook_ledger_records(None, at_ms=7, outcome="signature_rejected")

    assert records == [
        {"kind": "request", "at_ms": 7, "outcome": "signature_rejected", "fields": [], "n_messages": 0, "n_statuses": 0}
    ]


def test_every_message_of_a_batched_body_is_recorded_as_seen_with_its_ad_referral() -> None:
    body = _body(
        _change("messages", {"messages": [_message("wamid.A", "573001234567", referral=_AD_REFERRAL)]}),
        _change("messages", {"messages": [_message("wamid.B", "573000000001")]}),
    )

    records = webhook_ledger_records(body, at_ms=1_789_992_000_000)

    seen = _messages(records)
    assert [(m["stage"], m["wa_message_id"], m["session_id"]) for m in seen] == [
        ("seen", "wamid.A", "wa_573001234567"),
        ("seen", "wamid.B", "wa_573000000001"),
    ]
    assert seen[0]["referral"] == {
        "source_type": "ad",
        "source_id": "120200000000000001",
        "headline": "Velas aromáticas",
        "has_clid": True,
    }
    assert seen[1]["referral"] is None
    assert all(m["at_ms"] == 1_789_992_000_000 and m["field"] == "messages" for m in seen)


# ── lector: el resumen que reconcilia contra Meta ─────────────────────────────

from src.plugins.chats.agent.sales.use_cases.inbound_ledger import summarize_ledger  # noqa: E402

_AD = "120200000000000001"
# 2026-09-18 07:10 Bogotá = 12:10Z; 23:30 Bogotá del 17 = 04:30Z del 18.
_SEP_18_0710_BOG = 1_789_733_400_000
_SEP_17_2330_BOG = 1_789_705_800_000


def _seen(wamid: str, session: str, at_ms: int, ad: str | None) -> dict[str, Any]:
    referral = {"source_type": "ad", "source_id": ad, "headline": "x", "has_clid": True} if ad else None
    return {"kind": "message", "stage": "seen", "at_ms": at_ms, "field": "messages", "wa_message_id": wamid, "session_id": session, "referral": referral}


def _stage(stage: str, wamid: str, session: str, at_ms: int, error: str | None = None) -> dict[str, Any]:
    rec = {"kind": "message", "stage": stage, "at_ms": at_ms, "field": "messages", "wa_message_id": wamid, "session_id": session}
    return rec | ({"error": error} if error else {})


def test_summary_tells_arrived_vs_lost_inside_per_bogota_day_and_ad() -> None:
    t = _SEP_18_0710_BOG
    records = [
        {"kind": "request", "at_ms": t, "outcome": "accepted", "fields": ["messages"], "n_messages": 1, "n_statuses": 0},
        {"kind": "request", "at_ms": t, "outcome": "signature_rejected", "fields": [], "n_messages": 0, "n_statuses": 0},
        _seen("wamid.A", "wa_573001234567", t, _AD),
        _stage("ingested", "wamid.A", "wa_573001234567", t + 1),
        _seen("wamid.A", "wa_573001234567", t + 50, _AD),  # reintento de Meta: mismo wamid
        _seen("wamid.A2", "wa_573001234567", t + 60, None),  # 2º mensaje de la MISMA persona
        _stage("ingested", "wamid.A2", "wa_573001234567", t + 61),
        _seen("wamid.B", "wa_573000000001", t + 2, _AD),  # visto y nunca ingerido
        _seen("wamid.C", "wa_573000000002", t + 3, _AD),
        _stage("ingest_failed", "wamid.C", "wa_573000000002", t + 4, "RuntimeError: boom"),
        _seen("wamid.D", "wa_573000000003", _SEP_17_2330_BOG, _AD),
        _stage("ingested", "wamid.D", "wa_573000000003", _SEP_17_2330_BOG + 1),
    ]

    summary = summarize_ledger(records)

    assert summary["requests"] == {"accepted": 1, "signature_rejected": 1}
    assert summary["by_day_ad"] == {
        "2026-09-17": {_AD: {"sessions": 1, "ingested": 1, "failed": 0, "lost": 0}},
        "2026-09-18": {
            _AD: {"sessions": 3, "ingested": 1, "failed": 1, "lost": 1},
            "sin_referral": {"sessions": 1, "ingested": 1, "failed": 0, "lost": 0},
        },
    }
    assert summary["lost"] == [{"wa_message_id": "wamid.B", "session_id": "wa_573000000001", "at_ms": t + 2, "source_id": _AD}]
    assert summary["failed"] == [
        {"wa_message_id": "wamid.C", "session_id": "wa_573000000002", "at_ms": t + 3, "source_id": _AD, "error": "RuntimeError: boom"}
    ]


def test_summary_counts_a_parser_rejection_as_failed_with_its_reason() -> None:
    t = _SEP_18_0710_BOG
    records = [
        _seen("wamid.R", "wa_573001234567", t, _AD),
        _stage("rejected", "wamid.R", "wa_573001234567", t + 1, "text message missing 'text.body'"),
    ]

    summary = summarize_ledger(records)

    assert summary["by_day_ad"]["2026-09-18"][_AD] == {"sessions": 1, "ingested": 0, "failed": 1, "lost": 0}
    assert summary["failed"][0]["error"] == "text message missing 'text.body'"
    assert summary["lost"] == []
