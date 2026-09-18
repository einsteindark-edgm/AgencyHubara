"""Adapter filesystem del ledger de inbound: lo escrito se puede volver a leer
por ventana, y un disco roto jamás tumba el webhook."""
from __future__ import annotations

from src.plugins.chats.agent.sales.inbound_ledger_store import FilesystemInboundLedger

_DAY_MS = 86_400_000
_SEP_18 = 1_789_689_600_000  # 2026-09-18T00:00:00Z


def _rec(at_ms: int, wamid: str) -> dict:
    return {"kind": "message", "stage": "seen", "at_ms": at_ms, "wa_message_id": wamid}


def test_appended_records_are_read_back_by_window_across_day_files(tmp_path) -> None:
    ledger = FilesystemInboundLedger(tmp_path / "_ledger" / "webhook")
    ledger.append([_rec(_SEP_18 - 1, "wamid.17"), _rec(_SEP_18 + 5, "wamid.18a")])
    ledger.append([_rec(_SEP_18 + _DAY_MS + 1, "wamid.19")])
    ledger.append([_rec(_SEP_18 + 9, "wamid.18b")])

    got = ledger.read(since_ms=_SEP_18, until_ms=_SEP_18 + _DAY_MS)

    assert [r["wa_message_id"] for r in got] == ["wamid.18a", "wamid.18b"]
    assert sorted(p.name for p in (tmp_path / "_ledger" / "webhook").iterdir()) == [
        "2026-09-17.jsonl",
        "2026-09-18.jsonl",
        "2026-09-19.jsonl",
    ]


def test_an_unwritable_root_never_raises_into_the_webhook(tmp_path) -> None:
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    ledger = FilesystemInboundLedger(blocker / "webhook")

    ledger.append([_rec(_SEP_18, "wamid.X")])  # no lanza

    assert ledger.read(since_ms=0, until_ms=_SEP_18 + _DAY_MS) == []
