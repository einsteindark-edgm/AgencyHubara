"""Tests del adapter `FilesystemMessageHistoryStore`.

Operan sobre `tmp_path`. Verifican: append crea el JSONL, las lineas son JSON
parseables con shape `{"role": "user", "content": ...}`, multiples appends se
acumulan, ensure_ascii=False preserva caracteres no-ASCII (mismo shape que el
legado en `service.py`).
"""
from __future__ import annotations

import json

from src.platform.session_history import FilesystemMessageHistoryStore


def test_append_creates_jsonl_with_correct_shape(tmp_path):
    store = FilesystemMessageHistoryStore(tmp_path)
    store.append_user_event("wa_1", "hola")

    log = tmp_path / "wa_1" / "sessions" / "wa_1.jsonl"
    assert log.exists()
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {"role": "user", "content": "hola"}


def test_append_multiple_events_accumulates(tmp_path):
    store = FilesystemMessageHistoryStore(tmp_path)
    store.append_user_event("wa_1", "primer mensaje")
    store.append_user_event("wa_1", "segundo mensaje")
    store.append_user_event("wa_1", "tercero")

    log = tmp_path / "wa_1" / "sessions" / "wa_1.jsonl"
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    contents = [json.loads(line)["content"] for line in lines]
    assert contents == ["primer mensaje", "segundo mensaje", "tercero"]


def test_append_preserves_non_ascii(tmp_path):
    store = FilesystemMessageHistoryStore(tmp_path)
    store.append_user_event("wa_1", "ñandú está acá")

    log = tmp_path / "wa_1" / "sessions" / "wa_1.jsonl"
    raw = log.read_text(encoding="utf-8").strip()
    assert "ñandú" in raw  # ensure_ascii=False
    assert json.loads(raw) == {"role": "user", "content": "ñandú está acá"}


def test_append_isolates_per_session(tmp_path):
    store = FilesystemMessageHistoryStore(tmp_path)
    store.append_user_event("wa_a", "a1")
    store.append_user_event("wa_b", "b1")

    log_a = tmp_path / "wa_a" / "sessions" / "wa_a.jsonl"
    log_b = tmp_path / "wa_b" / "sessions" / "wa_b.jsonl"
    assert json.loads(log_a.read_text(encoding="utf-8").strip())["content"] == "a1"
    assert json.loads(log_b.read_text(encoding="utf-8").strip())["content"] == "b1"


def test_append_assistant_event_writes_role_and_content(tmp_path):
    store = FilesystemMessageHistoryStore(tmp_path)
    store.append_assistant_event("wa_1", "hola, soy el agente")

    log = tmp_path / "wa_1" / "sessions" / "wa_1.jsonl"
    assert log.exists()
    line = log.read_text(encoding="utf-8").strip()
    parsed = json.loads(line)
    assert parsed["role"] == "assistant"
    assert parsed["content"] == "hola, soy el agente"
    # timestamp ISO con TZ (...+00:00 o Z); shape de string parseable
    assert isinstance(parsed["timestamp"], str)
    assert "T" in parsed["timestamp"]
    # tool_calls no esta presente cuando no se pasa
    assert "tool_calls" not in parsed


def test_append_assistant_event_includes_tool_calls_when_provided(tmp_path):
    store = FilesystemMessageHistoryStore(tmp_path)
    tool_calls = [{"id": "tc_1", "type": "function", "function": {"name": "tag_session"}}]
    store.append_assistant_event("wa_1", "", tool_calls=tool_calls)

    log = tmp_path / "wa_1" / "sessions" / "wa_1.jsonl"
    parsed = json.loads(log.read_text(encoding="utf-8").strip())
    assert parsed["role"] == "assistant"
    assert parsed["tool_calls"] == tool_calls


def test_append_assistant_event_omits_empty_tool_calls(tmp_path):
    store = FilesystemMessageHistoryStore(tmp_path)
    store.append_assistant_event("wa_1", "hola", tool_calls=[])

    log = tmp_path / "wa_1" / "sessions" / "wa_1.jsonl"
    parsed = json.loads(log.read_text(encoding="utf-8").strip())
    assert "tool_calls" not in parsed


def test_append_mixed_user_and_assistant_events_preserves_order(tmp_path):
    store = FilesystemMessageHistoryStore(tmp_path)
    store.append_user_event("wa_1", "hola")
    store.append_assistant_event("wa_1", "hola! en qué te ayudo?")
    store.append_user_event("wa_1", "quiero info de un producto")
    store.append_assistant_event("wa_1", "claro, cuál?")

    log = tmp_path / "wa_1" / "sessions" / "wa_1.jsonl"
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 4
    roles = [json.loads(ln)["role"] for ln in lines]
    assert roles == ["user", "assistant", "user", "assistant"]


def test_append_assistant_preserves_non_ascii(tmp_path):
    store = FilesystemMessageHistoryStore(tmp_path)
    store.append_assistant_event("wa_1", "ñandú está acá 🦘")

    log = tmp_path / "wa_1" / "sessions" / "wa_1.jsonl"
    raw = log.read_text(encoding="utf-8").strip()
    assert "ñandú" in raw  # ensure_ascii=False
    assert "🦘" in raw
