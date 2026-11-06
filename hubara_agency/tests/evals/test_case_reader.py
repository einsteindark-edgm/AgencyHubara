"""The workshop of Ciclo Colossus reads the real conversation behind a card,
with the customer's personal data masked before anything leaves the box.

The card cites a case the way Calidad LLM shows it (`···4567 ep_001 t3`); the
reader finds that conversation in the vault and returns what was said and what
the bot did in that turn. Names, phones, addresses and emails come out masked,
the conversation id only by its last 4 digits. What the team needs to
reproduce the case stays: products, prices, quantities and cities.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.plugins.chats.agent.sales_eval import case
from src.plugins.chats.shared import turn_traces

SID = "wa_573001234567"
TWIN = "wa_twin_user_4567"  # same last 4 digits, not a phone
NAME = "Martha Gómez"


def _ms(hhmmss: str) -> int:
    return int(datetime.fromisoformat(f"2026-11-05T{hhmmss}+00:00").timestamp() * 1000)


def _iso(hhmmss: str) -> str:
    return datetime.fromtimestamp(_ms(hhmmss) / 1000, tz=timezone.utc).isoformat()


def _seed(vault: Path, sid: str = SID) -> None:
    sdir = vault / sid
    (sdir / "sessions").mkdir(parents=True)
    (sdir / "metadata.json").write_text(json.dumps({
        "profile": {"name": NAME},
        "episodes": [
            {"episode_id": "ep_001", "started_at_ms": _ms("15:00:00"),
             "order_draft": {"slots": {"nombre_recibe": "Lucía Pérez", "direccion": "Cra 7 # 45-12", "telefono": "3157654321"}}},
            {"episode_id": "ep_002", "started_at_ms": _ms("18:00:00")},
        ],
    }), encoding="utf-8")
    events = [
        {"role": "user", "content": "hola, quiero 6 cajas de velas de lavanda", "timestamp": _iso("15:00:00"), "wamid": "wamid.A"},
        {"role": "assistant", "content": f"¡Hola {NAME.split()[0]}! El envío a Medellín te cuesta $12.000", "timestamp": _iso("15:00:05"), "sender": "bot"},
        {"role": "user", "content": "las recibe Lucía Pérez en la Cra 7 # 45-12, cel 315 765 4321, lucia.p@gmail.com", "timestamp": _iso("15:01:00"), "wamid": "wamid.B"},
        {"role": "assistant", "content": "Ya te ayudo con eso", "timestamp": _iso("15:01:30"), "sender": "human"},
        {"role": "user", "content": "otra cosa", "timestamp": _iso("18:00:00"), "wamid": "wamid.C"},
    ]
    (sdir / "sessions" / f"{sid}.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")
    for trace in (
        {"episode_id": "ep_001", "turn": 1, "trigger": "customer", "turn_started_ms": _ms("15:00:01"), "workflow": "v2",
         "turn_key": f"{sid}/ep_001/t1",
         "inbound": [{"seq": 1, "wamid": "wamid.A", "ts_ms": _ms("15:00:00"), "kind": "text", "text": "hola, quiero 6 cajas de velas de lavanda"}],
         "steps": [{"kind": "tool", "name": "quote_shipping", "args": {"city": "Medellín", "boxes": 6}}]},
        {"episode_id": "ep_001", "turn": 3, "trigger": "customer", "turn_started_ms": _ms("15:01:01"), "workflow": "v2",
         "turn_key": f"{sid}/ep_001/t3",
         "inbound": [{"seq": 1, "wamid": "wamid.B", "ts_ms": _ms("15:01:00"), "kind": "text", "text": "las recibe Lucía Pérez en la Cra 7 # 45-12"}],
         "steps": [{"kind": "tool", "name": "register_order", "args": {"nombre_recibe": "Lucía Pérez", "telefono": "3157654321"}}]},
        {"episode_id": "ep_002", "turn": 1, "trigger": "customer", "turn_started_ms": _ms("18:00:01"), "workflow": "v2",
         "inbound": [{"seq": 1, "wamid": "wamid.C", "ts_ms": _ms("18:00:00"), "kind": "text", "text": "otra cosa"}]},
    ):
        turn_traces.append_trace(vault, sid, trace)


def _dump(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False)


def test_a_card_case_brings_back_its_conversation_with_personal_data_masked(tmp_path: Path) -> None:
    _seed(tmp_path)

    report = case.read_cases(tmp_path, ["4567:ep_001"])

    [found] = report["cases"]
    assert found["ref"] == "···4567 ep_001" and found["status"] == "ok"
    [conv] = found["conversations"]
    assert conv["conversation"] == "···4567" and conv["episode"] == "ep_001" and conv["bot"] == "nuevo"
    assert [m["who"] for m in conv["messages"]] == ["customer", "bot", "customer", "team"]
    text = _dump(report)
    for secret in ("573001234567", "Martha", "Gómez", "Lucía", "Pérez", "45-12", "765 4321", "3157654321", "lucia.p@gmail.com", "wamid."):
        assert secret not in text, secret
    # What reproduces the case stays.
    for kept in ("6 cajas de velas de lavanda", "Medellín", "$12.000", "quote_shipping"):
        assert kept in text, kept


def test_the_shipping_form_and_what_follows_it_come_out_masked(tmp_path: Path) -> None:
    """The receiver is known only from the form, as WhatsApp sends it, and
    the conversation keeps naming them afterwards."""
    sid = "wa_573009876543"
    sdir = tmp_path / sid
    (sdir / "sessions").mkdir(parents=True)
    (sdir / "metadata.json").write_text(json.dumps({
        "profile": {"name": "Caro"},
        "episodes": [{"episode_id": "ep_001", "started_at_ms": _ms("15:00:00")}],
    }), encoding="utf-8")
    form = (
        "[datos de envío recibidos] address=Cra 7#12_34; city=Guasca; national_id=1020304050; "
        "neighborhood=El Centro; phone=3001112233; receiver_name=Rosa Elena Díaz; "
        "delivery_notes=Dejar en portería. A nombre de ROSA ELENA DÍAZ"
    )
    events = [
        {"role": "user", "content": form, "timestamp": _iso("15:00:00")},
        {"role": "assistant", "content": "Listo Rosa, el envío a Guasca está incluido", "timestamp": _iso("15:00:05"), "sender": "bot"},
        {"role": "user", "content": "Carrer 7 #12_34.  Así", "timestamp": _iso("15:01:00")},
    ]
    (sdir / "sessions" / f"{sid}.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")
    turn_traces.append_trace(tmp_path, sid, {"episode_id": "ep_001", "turn": 1, "trigger": "customer", "turn_started_ms": _ms("15:00:01"), "workflow": "v2"})

    [conv] = case.read_cases(tmp_path, ["6543:ep_001"])["cases"][0]["conversations"]

    text = _dump(conv)
    for secret in ("Rosa", "ROSA", "Elena", "Díaz", "DÍAZ", "12_34", "1020304050", "3001112233", "El Centro"):
        assert secret not in text, secret
    for kept in ("city=Guasca", "el envío a Guasca está incluido", "Dejar en portería"):
        assert kept in text, kept
    # When it happened stays readable: a date is not a phone.
    assert [m["at"] for m in conv["messages"]] == [_iso("15:00:00"), _iso("15:00:05"), _iso("15:01:00")]


def test_a_turn_narrows_the_steps_but_keeps_the_whole_episode_as_context(tmp_path: Path) -> None:
    _seed(tmp_path)

    [conv] = case.read_cases(tmp_path, ["···4567 ep_001 t3"])["cases"][0]["conversations"]

    assert [t["turn"] for t in conv["turns"]] == [3]
    assert conv["turns"][0]["steps"][-1]["name"] == "register_order"
    assert len(conv["messages"]) == 4  # the episode, not the next one


def test_without_an_episode_only_the_episodes_are_listed_never_the_texts(tmp_path: Path) -> None:
    _seed(tmp_path)

    [found] = case.read_cases(tmp_path, ["4567"])["cases"]

    [conv] = found["conversations"]
    assert [e["episode"] for e in conv["episodes"]] == ["ep_001", "ep_002"]
    assert "messages" not in conv and "turns" not in conv


def test_two_conversations_with_the_same_last_digits_are_both_shown_as_ambiguous(tmp_path: Path) -> None:
    _seed(tmp_path)
    _seed(tmp_path, TWIN)

    [found] = case.read_cases(tmp_path, ["4567:ep_001:t1"])["cases"]

    assert found["status"] == "ambiguous" and len(found["conversations"]) == 2
    assert "twin_user" not in _dump(found)


def test_an_unknown_case_says_so(tmp_path: Path) -> None:
    _seed(tmp_path)

    assert case.read_cases(tmp_path, ["0000:ep_001"])["cases"][0]["status"] == "not_found"
    assert case.read_cases(tmp_path, ["4567:ep_009"])["cases"][0]["status"] == "not_found"


def test_only_a_few_cases_per_call_so_it_never_becomes_a_bulk_dump(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        case.read_cases(tmp_path, [f"{n:04d}:ep_001" for n in range(case.MAX_REFS + 1)])


def test_the_command_prints_the_masked_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _seed(tmp_path)
    monkeypatch.setattr(case, "_vault_dir", lambda: tmp_path)

    assert case.main(["4567:ep_001:t1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["cases"][0]["conversations"][0]["turns"][0]["turn"] == 1
    assert case.main([f"{n:04d}" for n in range(case.MAX_REFS + 1)]) == 2
