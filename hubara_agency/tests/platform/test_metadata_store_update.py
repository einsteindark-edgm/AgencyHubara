"""`FilesystemMetadataStore.update()` — read-modify-write atómico con lock.

Premortem 2026-07-14 (PM2-B8/B2): los marker-writes del handoff (cmid,
outbound_media) hacían read→mutate→write sin lock — dos writers concurrentes
se pisaban (lost update), y una lectura fresca que devolviera `{}` (OSError
transitorio) hacía que el write borrara TODA la metadata (active_route=humano
incluido → el bot revive en medio de la intervención).
"""
from __future__ import annotations

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
