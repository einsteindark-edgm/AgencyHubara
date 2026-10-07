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
# Un `metadata.json` dañado se recupera solo con la última copia buena
# (`metadata.json.prev`): decisión del operador, 2026-10-06. Las pruebas de la
# recuperación están en `test_metadata_store_recovery.py`; acá, las que vienen
# de la revisión y siguen valiendo.

_SESSION = {
    "active_route": "humano",
    "tag": "HUMANO",
    "episodes": [{"episode_id": "ep_001", "closed_at_ms": None}],
    "registered_order": {"order_id": "order_1"},
}


def _damaged_by_broken_json(path):
    path.write_text('{"active_route": "humano", "episodes": [', encoding="utf-8")


def _damaged_by_permissions(path):
    os.chmod(path, 0)


_DAMAGED = [
    pytest.param(_damaged_by_broken_json, id="json-roto"),
    pytest.param(
        _damaged_by_permissions,
        id="sin-permiso",
        marks=pytest.mark.skipif(os.geteuid() == 0, reason="root lee archivos 000"),
    ),
]


def _disk_bytes(path) -> bytes:
    os.chmod(path, 0o644)
    return path.read_bytes()


@pytest.mark.parametrize("damage", _DAMAGED)
def test_the_flush_failure_history_does_not_revive_the_bot(tmp_path, damage):
    """La reproducción de la revisión: el histórico de fallos del flush sobre
    una sesión humana con `metadata.json` dañado. Antes quedaba solo
    `{"ui_intents_failures": [...]}` y el bot revivía; ahora el fallo se anota
    sobre la última copia buena y la sesión sigue en humano."""
    from src.plugins.chats.agent.sales.activities import flush_ui_intents

    store = FilesystemMetadataStore(tmp_path)
    store.write("wa_1", _SESSION)
    store.write("wa_1", _SESSION)  # la segunda escritura deja la copia buena
    path = tmp_path / "wa_1" / "metadata.json"
    damage(path)

    flush_ui_intents._append_failures(store, "wa_1", [{"kind": "product_detail", "error": "x"}])

    after = json.loads(_disk_bytes(path))
    assert (after["active_route"], after["registered_order"]) == ("humano", {"order_id": "order_1"})
    assert after["ui_intents_failures"][-1]["kind"] == "product_detail"


def test_update_retries_a_transient_read_failure(tmp_path, monkeypatch):
    """Octava revisión del PR #393: quien escribe reintenta, bajo el candado y
    con esperas cortas, un error pasajero (como releía a11a150b). `read()` no."""
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


def test_the_escalation_tool_still_escalates_over_a_damaged_document(tmp_path):
    from exoclaw.agent.tools import ToolContext

    from src.platform.tools.escalation import EscalateToHumanTool

    path = tmp_path / "wa_1" / "metadata.json"
    path.parent.mkdir(parents=True)
    _damaged_by_broken_json(path)
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
