"""`FilesystemMetadataStore.update()` — read-modify-write atómico con lock.

Premortem 2026-07-14 (PM2-B8/B2): los marker-writes del handoff (cmid,
outbound_media) hacían read→mutate→write sin lock — dos writers concurrentes
se pisaban (lost update), y una lectura fresca que devolviera `{}` (OSError
transitorio) hacía que el write borrara TODA la metadata (active_route=humano
incluido → el bot revive en medio de la intervención).
"""
from __future__ import annotations

import copy
import threading

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


def test_write_merged_never_writes_a_partial_document_over_an_unreadable_one(tmp_path):
    """Lectura fresca ilegible para una sesión que tenía datos: no se mezcla
    sobre la nada (dejaría solo las llaves del escritor); se escribe lo que el
    escritor tiene, como antes."""
    store = FilesystemMetadataStore(tmp_path)
    store.write("wa_1", {"active_route": "humano", "n": 0})
    base = store.read("wa_1")
    ours = {**base, "n": 1}
    (tmp_path / "wa_1" / "metadata.json").write_text("{corrupto", encoding="utf-8")

    store.write_merged("wa_1", base=base, ours=ours)

    assert store.read("wa_1") == {"active_route": "humano", "n": 1}


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
