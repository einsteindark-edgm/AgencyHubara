"""`python -m src.plugins.chats.agent.sales.inbound_ledger_report` — el comando
que en la próxima revisión responde "¿Meta contó N y nosotros M: a dónde se
fueron?". Ventana por día de Bogotá (ambos inclusive); teléfonos enmascarados
salvo `--full`."""
from __future__ import annotations

import json

from src.plugins.chats.agent.sales import inbound_ledger_report as report
from src.plugins.chats.agent.sales.inbound_ledger_store import FilesystemInboundLedger

_AD = "120200000000000001"
_SEP_18_0710_BOG = 1_789_733_400_000
_SEP_19_0010_BOG = 1_789_794_600_000  # fuera de la ventana del 18


def _seen(wamid: str, session: str, at_ms: int) -> dict:
    referral = {"source_type": "ad", "source_id": _AD, "headline": "x", "has_clid": True}
    return {"kind": "message", "stage": "seen", "at_ms": at_ms, "field": "messages", "wa_message_id": wamid, "session_id": session, "referral": referral}


def test_report_covers_the_bogota_day_window_and_masks_phones(tmp_path, monkeypatch, capsys) -> None:
    ledger = FilesystemInboundLedger(tmp_path)
    ledger.append([_seen("wamid.B", "wa_573000000001", _SEP_18_0710_BOG), _seen("wamid.Z", "wa_573000000004", _SEP_19_0010_BOG)])
    monkeypatch.setattr(report, "build_inbound_ledger", lambda: ledger)

    assert report.main(["--from", "2026-09-18", "--to", "2026-09-18"]) == 0

    out = json.loads(capsys.readouterr().out)
    assert out["by_day_ad"] == {"2026-09-18": {_AD: {"sessions": 1, "ingested": 0, "failed": 0, "lost": 1}}}
    assert out["lost"][0]["session_id"] == "wa_***0001"
    assert out["lost"][0]["at_bogota"] == "2026-09-18 07:10:00"


def test_full_flag_shows_the_session_id_to_locate_the_chat(tmp_path, monkeypatch, capsys) -> None:
    ledger = FilesystemInboundLedger(tmp_path)
    ledger.append([_seen("wamid.B", "wa_573000000001", _SEP_18_0710_BOG)])
    monkeypatch.setattr(report, "build_inbound_ledger", lambda: ledger)

    report.main(["--from", "2026-09-18", "--to", "2026-09-18", "--full"])

    assert json.loads(capsys.readouterr().out)["lost"][0]["session_id"] == "wa_573000000001"
