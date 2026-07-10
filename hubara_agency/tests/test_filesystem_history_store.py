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
    parsed = json.loads(lines[0])
    assert parsed["role"] == "user"
    assert parsed["content"] == "hola"
    # HU-WA24H-001 F1.2: user events ahora incluyen timestamp ISO UTC
    # (simetria con assistant — el campo se necesita para tracking de
    # service window 24h + tiempo de respuesta del agente).
    assert isinstance(parsed["timestamp"], str)
    assert "T" in parsed["timestamp"]


def test_append_user_event_timestamp_is_iso_utc(tmp_path):
    """User event timestamp es ISO con TZ (formato Python isoformat)."""
    store = FilesystemMessageHistoryStore(tmp_path)
    store.append_user_event("wa_1", "hola")

    log = tmp_path / "wa_1" / "sessions" / "wa_1.jsonl"
    parsed = json.loads(log.read_text(encoding="utf-8").strip())
    ts = parsed["timestamp"]
    # ISO 8601 con TZ +00:00 (UTC)
    assert ts.endswith("+00:00")
    # Parseable round-trip por datetime
    from datetime import datetime

    parsed_dt = datetime.fromisoformat(ts)
    assert parsed_dt.utcoffset().total_seconds() == 0  # type: ignore[union-attr]


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
    parsed = json.loads(raw)
    assert parsed["role"] == "user"
    assert parsed["content"] == "ñandú está acá"
    # HU-WA24H-001 F1.2: timestamp ahora presente en user events.
    assert "timestamp" in parsed


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


def test_append_human_event_writes_assistant_role_with_sender_marker(tmp_path):
    """append_human_event escribe role=assistant + sender=human + timestamp.

    Esto es la pieza clave del handoff: para el LLM (al retomar el chat) los
    mensajes humanos lucen como assistant natural; para el dashboard, el
    campo `sender=human` permite proyectarlos a un `ui_type` distinto y
    pintarlos diferenciados.
    """
    store = FilesystemMessageHistoryStore(tmp_path)
    store.append_human_event("wa_1", "Hola, te ayudo personalmente con tu pedido")

    log = tmp_path / "wa_1" / "sessions" / "wa_1.jsonl"
    parsed = json.loads(log.read_text(encoding="utf-8").strip())
    assert parsed["role"] == "assistant"
    assert parsed["sender"] == "human"
    assert parsed["content"] == "Hola, te ayudo personalmente con tu pedido"
    assert isinstance(parsed["timestamp"], str)
    assert "T" in parsed["timestamp"]


def test_append_human_event_persists_image_url_when_provided(tmp_path):
    """El operador puede mandar una FOTO desde el dashboard: cuando se pasa
    `image_url` (ref al media store outbound), se persiste en el evento humano
    para que el dashboard re-renderice la burbuja con la imagen (simetría con
    `append_user_event(image_url=...)` que ya existe para inbound)."""
    store = FilesystemMessageHistoryStore(tmp_path)
    store.append_human_event(
        "wa_1",
        "Mirá el color real 🤍",
        image_url="/api/dashboard/media/wa_1/out-abc.jpg",
    )

    log = tmp_path / "wa_1" / "sessions" / "wa_1.jsonl"
    parsed = json.loads(log.read_text(encoding="utf-8").strip())
    assert parsed["role"] == "assistant"
    assert parsed["sender"] == "human"
    assert parsed["content"] == "Mirá el color real 🤍"
    assert parsed["image_url"] == "/api/dashboard/media/wa_1/out-abc.jpg"


def test_append_human_event_omits_image_url_when_absent(tmp_path):
    """Sin `image_url` (mensaje de solo texto), el campo NO aparece — no
    ensuciamos el JSONL con nulls que el clasificador tenga que ignorar."""
    store = FilesystemMessageHistoryStore(tmp_path)
    store.append_human_event("wa_1", "solo texto")

    log = tmp_path / "wa_1" / "sessions" / "wa_1.jsonl"
    parsed = json.loads(log.read_text(encoding="utf-8").strip())
    assert "image_url" not in parsed


def test_append_human_event_preserves_non_ascii(tmp_path):
    store = FilesystemMessageHistoryStore(tmp_path)
    store.append_human_event("wa_1", "Voy a verificar el envío 🤍")

    log = tmp_path / "wa_1" / "sessions" / "wa_1.jsonl"
    raw = log.read_text(encoding="utf-8").strip()
    assert "envío" in raw
    assert "🤍" in raw


def test_append_human_event_interleaves_with_user_and_assistant_events(tmp_path):
    """Verifica que human_events conviven con user/assistant en el JSONL."""
    store = FilesystemMessageHistoryStore(tmp_path)
    store.append_user_event("wa_1", "hola")
    store.append_assistant_event("wa_1", "respuesta del bot")
    store.append_user_event("wa_1", "necesito hablar con alguien")
    store.append_human_event("wa_1", "Hola, soy del equipo 🤍")
    store.append_user_event("wa_1", "gracias")

    log = tmp_path / "wa_1" / "sessions" / "wa_1.jsonl"
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 5
    events = [json.loads(ln) for ln in lines]
    # 4to evento es del humano y conserva el marker
    assert events[3]["role"] == "assistant"
    assert events[3]["sender"] == "human"
    # 2do evento es del bot, sin marker sender
    assert events[1]["role"] == "assistant"
    assert "sender" not in events[1]
