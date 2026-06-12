"""Contract tests del AttributionReadPort — la MISMA suite contra fs y fake.

Regla del ConnectorKit: ningún port sin fake, y el fake debe comportarse
IGUAL que el adapter real ante el mismo contrato (si divergen, lo caza esta
suite, no producción). El adapter filesystem replica además la semántica
mtime-superset heredada del scan de ads (verificada por sus tests de
agregación, que siguen pasando intactos post-rewire).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.platform.attribution import (
    AttributionReadPort,
    AttributionSession,
    FilesystemAttributionStore,
    InMemoryAttributionStore,
)


def _mk_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    for sid, meta in (
        ("wa_573001112233", {"origin": {"channel": "ad", "source_id": "AD_A"}}),
        ("wa_573009998877", {"origin": {"channel": "direct"}}),
    ):
        d = vault / sid
        d.mkdir(parents=True)
        (d / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    # ruido que el scan debe ignorar:
    (vault / "no_wa_dir").mkdir()
    corrupt = vault / "wa_corrupta"
    corrupt.mkdir()
    (corrupt / "metadata.json").write_text("{not json", encoding="utf-8")
    return vault


def _fs_store(tmp_path: Path) -> FilesystemAttributionStore:
    return FilesystemAttributionStore(_mk_vault(tmp_path))


def _fake_store(tmp_path: Path) -> InMemoryAttributionStore:
    fs = _fs_store(tmp_path)
    return InMemoryAttributionStore(fs.scan_sessions())


@pytest.mark.parametrize("make_store", [_fs_store, _fake_store])
def test_contract_scan_returns_parsed_sessions(tmp_path: Path, make_store) -> None:
    store: AttributionReadPort = make_store(tmp_path)
    assert isinstance(store, AttributionReadPort)  # structural conformance
    sessions = store.scan_sessions()
    ids = {s.session_id for s in sessions}
    assert ids == {"wa_573001112233", "wa_573009998877"}  # corrupta + no_wa fuera
    by_id = {s.session_id: s for s in sessions}
    assert by_id["wa_573001112233"].metadata["origin"]["source_id"] == "AD_A"
    assert by_id["wa_573001112233"].phone == "573001112233"


def test_fs_since_ms_is_superset_prefilter(tmp_path: Path) -> None:
    vault = _mk_vault(tmp_path)
    store = FilesystemAttributionStore(vault)
    old = vault / "wa_573001112233" / "metadata.json"
    past = 1_000_000_000  # epoch s viejo
    os.utime(old, (past, past))
    fresh = store.scan_sessions(since_ms=1_500_000_000_000)
    ids = {s.session_id for s in fresh}
    assert "wa_573001112233" not in ids  # mtime viejo → skipped sin parsear
    assert "wa_573009998877" in ids


def test_fake_since_ms_mirrors_superset_semantics(tmp_path: Path) -> None:
    sessions = [
        AttributionSession("wa_a", tmp_path, {"k": 1}),
        AttributionSession("wa_b", tmp_path, {"k": 2}),
    ]
    fake = InMemoryAttributionStore(sessions, touched_ms={"wa_a": 100})
    ids = {s.session_id for s in fake.scan_sessions(since_ms=500)}
    # wa_a tocada antes del since → fuera; wa_b SIN info de touch → NUNCA se
    # saltea (superset, igual que el defensivo del fs ante stat fallido).
    assert ids == {"wa_b"}


def test_fs_vault_inexistente_devuelve_vacio(tmp_path: Path) -> None:
    store = FilesystemAttributionStore(tmp_path / "no_existe")
    assert store.scan_sessions() == []
