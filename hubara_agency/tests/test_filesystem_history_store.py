"""Tests del adapter `FilesystemMessageHistoryStore`.

Operan sobre `tmp_path`. Verifican: append crea el JSONL, las lineas son JSON
parseables con shape `{"role": "user", "content": ...}`, multiples appends se
acumulan, ensure_ascii=False preserva caracteres no-ASCII (mismo shape que el
legado en `service.py`).
"""
from __future__ import annotations

import json

from src.domains.sales_whatsapp.infrastructure.storage import (
    FilesystemMessageHistoryStore,
)


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
