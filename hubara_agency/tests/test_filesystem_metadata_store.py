"""Tests del adapter `FilesystemMetadataStore`.

Operan sobre `tmp_path` (no tocan el vault real). Verifican: round-trip
read/write, mkdir automatico de la carpeta de la sesion, missing -> {} y
archivos corruptos -> {} (mismo comportamiento que el legado en `service.py`).
"""
from __future__ import annotations

import json

from src.sales_whatsapp.state import FilesystemMetadataStore


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
