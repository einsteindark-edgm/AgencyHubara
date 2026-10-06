"""`FilesystemMetadataStore.update()` — read-modify-write atómico con lock.

Premortem 2026-07-14 (PM2-B8/B2): los marker-writes del handoff (cmid,
outbound_media) hacían read→mutate→write sin lock — dos writers concurrentes
se pisaban (lost update), y una lectura fresca que devolviera `{}` (OSError
transitorio) hacía que el write borrara TODA la metadata (active_route=humano
incluido → el bot revive en medio de la intervención).
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import threading

import pytest

from src.platform.state import FilesystemMetadataStore


def test_update_reads_fresh_and_writes(tmp_path):
    store = FilesystemMetadataStore(tmp_path)
    store.write("wa_1", {"active_route": "humano", "n": 0})

    def mutator(data):
        data["n"] = data["n"] + 1
        return data

    result = store.update("wa_1", mutator)

    assert result is not None and result["n"] == 1
    persisted = store.read("wa_1")
    assert persisted["n"] == 1
    assert persisted["active_route"] == "humano"


def test_update_abort_does_not_write(tmp_path):
    """El mutator devuelve None → NO se escribe nada (guard anti-clobber)."""
    store = FilesystemMetadataStore(tmp_path)
    store.write("wa_1", {"active_route": "humano"})

    def mutator(data):
        # Simula el guard: la lectura vino "vacía" según el caller → abortar.
        return None

    assert store.update("wa_1", mutator) is None
    assert store.read("wa_1") == {"active_route": "humano"}


def test_update_serializes_concurrent_writers(tmp_path):
    """N threads incrementando un contador vía update() no pierden updates.

    Con read→mutate→write sin lock este test es flaky-rojo (lost updates);
    con flock los 20 incrementos sobreviven siempre.
    """
    store = FilesystemMetadataStore(tmp_path)
    store.write("wa_1", {"count": 0})

    def bump():
        store.update("wa_1", lambda d: {**d, "count": d.get("count", 0) + 1})

    threads = [threading.Thread(target=bump) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert store.read("wa_1")["count"] == 20


def test_update_creates_session_dir_if_missing(tmp_path):
    store = FilesystemMetadataStore(tmp_path)
    result = store.update("wa_new", lambda d: {**d, "seed": True})
    assert result == {"seed": True}
    assert store.read("wa_new") == {"seed": True}


# --- incidente 2026-10-06: ninguna escritura cae a mitad de un update() -------


def test_write_waits_for_an_update_in_progress(tmp_path):
    """Un `write()` no puede caer entre la lectura y la escritura de un
    `update()`: el `update()` escribiría después lo que leyó antes y borraría
    lo del `write()` (así volvió a la cola una foto ya entregada)."""
    store = FilesystemMetadataStore(tmp_path)
    store.write("wa_1", {"pending_ui_intents": [{"id": "foto"}]})
    inside = threading.Event()
    release = threading.Event()

    def slow_mutator(data):
        inside.set()
        release.wait(5)
        data["delivery"] = "sent"
        return data

    updater = threading.Thread(target=store.update, args=("wa_1", slow_mutator))
    updater.start()
    assert inside.wait(5)
    writer = threading.Thread(target=store.write, args=("wa_1", {"pending_ui_intents": []}))
    writer.start()
    writer.join(0.3)
    blocked = writer.is_alive()
    release.set()
    updater.join(5)
    writer.join(5)

    assert blocked, "write() tiene que esperar el candado del update() en curso"
    assert store.read("wa_1") == {"pending_ui_intents": []}


def test_write_merged_keeps_what_another_writer_wrote_meanwhile(tmp_path):
    """El escritor que acumula cambios (el ingest esperando a Jev) solo lleva
    lo suyo: lo que el flush sacó de la cola mientras tanto no vuelve."""
    store = FilesystemMetadataStore(tmp_path)
    store.write("wa_1", {"pending_ui_intents": [{"id": "foto"}], "outbound_media_index": {}, "n": 0})
    base = store.read("wa_1")
    ours = copy.deepcopy(base)
    ours["n"] = 1
    ours["last_inbound_message_id"] = "wamid.in"

    def flush_pops_the_photo(data):
        data["pending_ui_intents"] = []
        data["outbound_media_index"] = {"wamid.foto": {"handle": "cubo-love"}}
        return data

    store.update("wa_1", flush_pops_the_photo)

    written = store.write_merged("wa_1", base=base, ours=ours)

    expected = {
        "pending_ui_intents": [],
        "outbound_media_index": {"wamid.foto": {"handle": "cubo-love"}},
        "n": 1,
        "last_inbound_message_id": "wamid.in",
    }
    assert store.read("wa_1") == expected
    assert written == expected


def test_write_merged_waits_for_an_update_in_progress(tmp_path):
    store = FilesystemMetadataStore(tmp_path)
    store.write("wa_1", {"n": 0})
    base = store.read("wa_1")
    inside = threading.Event()
    release = threading.Event()

    def slow_mutator(data):
        inside.set()
        release.wait(5)
        data["delivery"] = "sent"
        return data

    updater = threading.Thread(target=store.update, args=("wa_1", slow_mutator))
    updater.start()
    assert inside.wait(5)
    merger = threading.Thread(target=store.write_merged, args=("wa_1",), kwargs={"base": base, "ours": {"n": 1}})
    merger.start()
    merger.join(0.3)
    blocked = merger.is_alive()
    release.set()
    updater.join(5)
    merger.join(5)

    assert blocked
    assert store.read("wa_1") == {"n": 1, "delivery": "sent"}


def test_write_merged_does_not_write_a_stale_view_over_an_unreadable_document(tmp_path):
    """Segunda revisión del PR #393: lectura fresca ilegible para una sesión
    que el escritor sí había leído. Escribir su copia (`ours`) revertiría lo
    que pasó desde que la leyó —p. ej. la toma de un humano—: misma regla que
    `update()`, no se escribe (salvo `overwrite_unreadable=True`)."""
    store = FilesystemMetadataStore(tmp_path)
    store.write("wa_1", {"active_route": "ventas", "tag": "NO_ETIQUETADO"})
    base = store.read("wa_1")
    ours = {**base, "last_inbound_message_id": "wamid.x"}
    path = tmp_path / "wa_1" / "metadata.json"
    path.write_text('{"active_route": "humano", "tag": "HUMANO", "motivo": "lo', encoding="utf-8")
    on_disk = path.read_bytes()

    assert store.write_merged("wa_1", base=base, ours=ours) is None
    assert path.read_bytes() == on_disk, "escribió su vista vieja sobre la toma del humano"


def test_a_nested_write_in_the_same_thread_does_not_hang(tmp_path):
    """Un mutator que (por error) escribe la misma sesión no cuelga el worker:
    el candado es reentrante dentro del mismo hilo."""
    store = FilesystemMetadataStore(tmp_path)
    store.write("wa_1", {"n": 0})

    def mutator(data):
        store.write("wa_1", {"n": 99})
        store.update("wa_1", lambda d: {**d, "m": 1})
        data["n"] = 1
        return data

    worker = threading.Thread(target=store.update, args=("wa_1", mutator), daemon=True)
    worker.start()
    worker.join(5)

    assert not worker.is_alive(), "una escritura anidada en el mismo hilo colgó el candado"
    assert store.read("wa_1") == {"n": 1}


# --- revisión del PR #393 ------------------------------------------------------
# D1: una lectura fallida NO es una sesión vacía. `read()` devuelve `{}` ante
# OSError/JSON roto, y `update()` le pasaba ese `{}` al mutator y escribía un
# documento casi vacío: con `active_route=humano`, episodios y pedido, el flush
# dejaba `{"ui_intents_failures": [...]}` y el bot revivía.

_SESSION = {
    "active_route": "humano",
    "tag": "HUMANO",
    "episodes": [{"episode_id": "ep_001", "closed_at_ms": None}],
    "registered_order": {"order_id": "order_1"},
}


def _unreadable_by_broken_json(path):
    path.write_text('{"active_route": "humano", "episodes": [', encoding="utf-8")


def _unreadable_by_permissions(path):
    os.chmod(path, 0)


_UNREADABLE = [
    pytest.param(_unreadable_by_broken_json, id="json-roto"),
    pytest.param(
        _unreadable_by_permissions,
        id="sin-permiso",
        marks=pytest.mark.skipif(os.geteuid() == 0, reason="root lee archivos 000"),
    ),
]


def _disk_bytes(path) -> bytes:
    os.chmod(path, 0o644)
    return path.read_bytes()


@pytest.mark.parametrize("break_it", _UNREADABLE)
def test_update_never_writes_over_a_document_it_could_not_read(tmp_path, break_it):
    store = FilesystemMetadataStore(tmp_path)
    store.write("wa_1", _SESSION)
    path = tmp_path / "wa_1" / "metadata.json"
    break_it(path)
    calls = []

    def mutator(data):
        calls.append(dict(data))
        data["ui_intents_failures"] = [{"kind": "product_detail", "error": "x"}]
        return data

    assert store.update("wa_1", mutator) is None
    assert calls == [], "el mutator no puede recibir un {} en lugar de la sesión"
    assert b"ui_intents_failures" not in _disk_bytes(path)


@pytest.mark.parametrize("break_it", _UNREADABLE)
def test_the_flush_failure_history_does_not_revive_the_bot(tmp_path, break_it):
    """La reproducción de la revisión: el histórico de fallos del flush sobre
    una sesión humana con `metadata.json` ilegible."""
    from src.plugins.chats.agent.sales.activities import flush_ui_intents

    store = FilesystemMetadataStore(tmp_path)
    store.write("wa_1", _SESSION)
    path = tmp_path / "wa_1" / "metadata.json"
    break_it(path)

    flush_ui_intents._append_failures(store, "wa_1", [{"kind": "product_detail", "error": "x"}])

    after = _disk_bytes(path)
    assert b"ui_intents_failures" not in after
    assert b'"active_route": "humano"' in after


def test_update_retries_a_transient_read_failure_once(tmp_path, monkeypatch):
    from pathlib import Path

    store = FilesystemMetadataStore(tmp_path)
    store.write("wa_1", _SESSION)
    real_read_text = Path.read_text
    failures = {"left": 1}

    def flaky_read_text(self, *args, **kwargs):
        if self.name == "metadata.json" and failures["left"]:
            failures["left"] -= 1
            raise OSError(5, "Input/output error")  # EIO transitorio
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)

    written = store.update("wa_1", lambda d: {**d, "n": 1})

    assert written == {**_SESSION, "n": 1}
    assert store.read("wa_1") == {**_SESSION, "n": 1}


def test_update_of_a_missing_document_still_creates_it(tmp_path):
    store = FilesystemMetadataStore(tmp_path)

    written = store.update("wa_nueva", lambda d: {**d, "phone_number_id": "pnid"})

    assert written == {"phone_number_id": "pnid"}
    assert store.read("wa_nueva") == {"phone_number_id": "pnid"}


@pytest.mark.parametrize("break_it", _UNREADABLE)
def test_who_must_write_anyway_asks_for_it_explicitly(tmp_path, break_it):
    """La escalación a humano reescribe a propósito un documento ilegible
    (sin ella el cliente queda con el bot): lo pide con `overwrite_unreadable`."""
    store = FilesystemMetadataStore(tmp_path)
    store.write("wa_1", _SESSION)
    path = tmp_path / "wa_1" / "metadata.json"
    break_it(path)

    written = store.update(
        "wa_1", lambda d: {**d, "active_route": "humano"}, overwrite_unreadable=True
    )

    assert written == {"active_route": "humano"}
    assert json.loads(_disk_bytes(path)) == {"active_route": "humano"}


@pytest.mark.parametrize("break_it", _UNREADABLE)
def test_write_merged_does_not_write_when_neither_side_could_read(tmp_path, break_it):
    """Sin nada leído (`base` vacío) y el disco ilegible, escribir `ours` sería
    dejar solo las llaves del escritor: no se escribe."""
    store = FilesystemMetadataStore(tmp_path)
    store.write("wa_1", _SESSION)
    path = tmp_path / "wa_1" / "metadata.json"
    break_it(path)

    assert store.write_merged("wa_1", base={}, ours={"active_route": "ventas", "tag": "NO_ETIQUETADO"}) is None
    assert b"NO_ETIQUETADO" not in _disk_bytes(path)


def test_the_escalation_tool_still_escalates_over_an_unreadable_document(tmp_path):
    from exoclaw.agent.tools import ToolContext

    from src.platform.tools.escalation import EscalateToHumanTool

    path = tmp_path / "wa_1" / "metadata.json"
    path.parent.mkdir(parents=True)
    _unreadable_by_broken_json(path)
    tool = EscalateToHumanTool(workspace=str(tmp_path), vault_dir=tmp_path)
    ctx = ToolContext(session_key="wa_1", channel="whatsapp", chat_id="wa_1")

    asyncio.run(tool.execute_with_context(ctx, reason_category="OTHER", summary="El cliente pide hablar con alguien"))

    data = json.loads(path.read_text(encoding="utf-8"))
    assert (data["active_route"], data["tag"]) == ("humano", "HUMANO")


# M5: la reentrancia del candado reconoce el MISMO archivo por cualquier ruta
# (symlink) y avisa cuando una escritura anidada se resuelve así.


def test_a_nested_write_through_another_path_to_the_same_file_does_not_hang(tmp_path, caplog):
    real = tmp_path / "vault"
    real.mkdir()
    link = tmp_path / "vault_link"
    link.symlink_to(real, target_is_directory=True)
    store = FilesystemMetadataStore(real)
    same_file = FilesystemMetadataStore(link)
    store.write("wa_1", {"n": 0})

    def mutator(data):
        same_file.write("wa_1", {"n": 99})
        data["n"] = 1
        return data

    caplog.set_level(logging.WARNING)
    worker = threading.Thread(target=store.update, args=("wa_1", mutator), daemon=True)
    worker.start()
    worker.join(5)

    assert not worker.is_alive(), "una escritura anidada por otra ruta al mismo archivo colgó el candado"
    assert store.read("wa_1") == {"n": 1}
    assert "metadata_nested_write" in caplog.text


# --- segunda revisión del PR #393 ------------------------------------------------
# Quien reescribe a propósito un documento ilegible (`overwrite_unreadable`) no
# lo destruye: un JSON truncado se repara a mano. El original queda al lado,
# tal cual, en `metadata.json.unreadable-<ms>`, y el log dice dónde.


def _backups(path) -> list:
    return sorted(path.parent.glob(f"{path.name}.unreadable-*"))


@pytest.mark.parametrize("break_it", _UNREADABLE)
def test_overwriting_an_unreadable_document_keeps_the_original_aside(tmp_path, break_it, caplog):
    store = FilesystemMetadataStore(tmp_path)
    store.write("wa_1", _SESSION)
    path = tmp_path / "wa_1" / "metadata.json"
    break_it(path)
    original = _disk_bytes(path)
    break_it(path)  # `_disk_bytes` le devuelve el permiso de lectura
    caplog.set_level(logging.WARNING)

    store.update("wa_1", lambda d: {**d, "active_route": "humano", "tag": "HUMANO"}, overwrite_unreadable=True)

    backups = _backups(path)
    assert len(backups) == 1, "se reescribió sin guardar el original"
    assert _disk_bytes(backups[0]) == original
    assert str(backups[0]) in caplog.text, "el log no dice dónde quedó el original"
    assert json.loads(_disk_bytes(path)) == {"active_route": "humano", "tag": "HUMANO"}


def test_write_merged_that_overwrites_also_keeps_the_original_aside(tmp_path):
    store = FilesystemMetadataStore(tmp_path)
    path = tmp_path / "wa_1" / "metadata.json"
    path.parent.mkdir(parents=True)
    _unreadable_by_broken_json(path)
    original = path.read_bytes()

    store.write_merged("wa_1", base={}, ours={"active_route": "humano"}, overwrite_unreadable=True)

    backups = _backups(path)
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert json.loads(path.read_text(encoding="utf-8")) == {"active_route": "humano"}


def test_a_document_that_is_not_utf8_is_unreadable_not_an_exception(tmp_path):
    """Un byte `\\xff` hacía lanzar `UnicodeDecodeError` a `read()`/`update()`:
    es un documento ilegible como cualquier otro."""
    store = FilesystemMetadataStore(tmp_path)
    store.write("wa_1", _SESSION)
    path = tmp_path / "wa_1" / "metadata.json"
    path.write_bytes(b'{"active_route": "humano", "x": "\xff\xfe"}')
    on_disk = path.read_bytes()

    assert store.read("wa_1") == {}
    assert store.update("wa_1", lambda d: {**d, "n": 1}) is None
    assert path.read_bytes() == on_disk
    assert store.update("wa_1", lambda d: {**d, "active_route": "humano"}, overwrite_unreadable=True) == {
        "active_route": "humano"
    }
    assert [b.read_bytes() for b in _backups(path)] == [on_disk]


def test_is_unreadable_tells_a_new_session_from_an_unreadable_one(tmp_path):
    """`read()` devuelve `{}` en los dos casos; quien tiene que distinguirlos
    (el ingest, una API que responde) pregunta."""
    store = FilesystemMetadataStore(tmp_path)
    store.write("wa_ok", _SESSION)
    path = tmp_path / "wa_roto" / "metadata.json"
    path.parent.mkdir(parents=True)
    _unreadable_by_broken_json(path)

    assert store.is_unreadable("wa_nueva") is False
    assert store.is_unreadable("wa_ok") is False
    assert store.is_unreadable("wa_roto") is True
