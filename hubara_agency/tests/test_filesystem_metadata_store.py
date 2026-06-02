"""Tests del adapter `FilesystemMetadataStore`.

Operan sobre `tmp_path` (no tocan el vault real). Verifican: round-trip
read/write, mkdir automatico de la carpeta de la sesion, missing -> {} y
archivos corruptos -> {} (mismo comportamiento que el legado en `service.py`).
Tambien la atomicidad del write (temp + os.replace) y `ensure_ascii=False`.
"""
from __future__ import annotations

import json

from src.platform.state import atomic_write_json
from src.plugins.chats.agent.sales.state import FilesystemMetadataStore


def test_read_returns_empty_dict_when_missing(tmp_path):
    store = FilesystemMetadataStore(tmp_path)
    assert store.read("wa_unknown") == {}


def test_write_creates_session_dir_and_round_trip(tmp_path):
    store = FilesystemMetadataStore(tmp_path)
    payload = {"active_route": "ventas", "phone_number_id": "PID"}

    store.write("wa_42", payload)

    metadata_path = tmp_path / "wa_42" / "metadata.json"
    assert metadata_path.exists()
    on_disk = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert on_disk == payload
    assert store.read("wa_42") == payload


def test_read_returns_empty_dict_when_corrupt(tmp_path):
    store = FilesystemMetadataStore(tmp_path)
    metadata_path = tmp_path / "wa_corrupt" / "metadata.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text("not-json{", encoding="utf-8")

    assert store.read("wa_corrupt") == {}


def test_read_returns_empty_dict_when_not_a_dict(tmp_path):
    store = FilesystemMetadataStore(tmp_path)
    metadata_path = tmp_path / "wa_list" / "metadata.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    assert store.read("wa_list") == {}


def test_write_overwrites_existing(tmp_path):
    store = FilesystemMetadataStore(tmp_path)
    store.write("wa_1", {"active_route": "ventas"})
    store.write("wa_1", {"active_route": "remarketing", "phone_number_id": "P"})

    assert store.read("wa_1") == {
        "active_route": "remarketing",
        "phone_number_id": "P",
    }


def test_write_keeps_accents_literal_on_disk(tmp_path):
    """ensure_ascii=False: enies/acentos quedan legibles, no escapados a \\u."""
    store = FilesystemMetadataStore(tmp_path)
    store.write("wa_acc", {"motivo": "señal de compañía"})
    raw = (tmp_path / "wa_acc" / "metadata.json").read_text(encoding="utf-8")
    assert "señal de compañía" in raw
    assert "\\u" not in raw


def test_write_leaves_no_temp_files(tmp_path):
    """El write atomico limpia su temp file (no deja `.metadata.json.*.tmp`)."""
    store = FilesystemMetadataStore(tmp_path)
    store.write("wa_tmp", {"a": 1})
    session_dir = tmp_path / "wa_tmp"
    leftovers = [
        p.name for p in session_dir.iterdir() if p.name != "metadata.json"
    ]
    assert leftovers == []


def test_atomic_write_json_roundtrip_creates_parents(tmp_path):
    path = tmp_path / "nested" / "deep" / "doc.json"
    atomic_write_json(path, {"x": [1, 2, 3], "y": "ñ"})
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "x": [1, 2, 3],
        "y": "ñ",
    }
