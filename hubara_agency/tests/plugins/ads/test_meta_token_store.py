"""Token store de la integración Meta (Graph) — contrato del port + fake in-memory.

El backend guarda el token OAuth de Meta (server-side) detrás de un port (R-DIP):
el vendor real es SSM SecureString; el fake in-memory es para tests. "Ningún port
sin fake". Single-tenant por ahora (un token); el SSM key será `/hubara/<tenant>/meta/oauth`.
"""
from __future__ import annotations

import dataclasses

import pytest

from src.plugins.ads.meta.token_store import InMemoryTokenStore, MetaToken


def _tok(**kw) -> MetaToken:
    base = dict(
        access_token="EAA-abc",
        expires_at=1782842400,
        scopes=("ads_read",),
        account_id="act_1010393601284112",
        account_name="Hubara",
    )
    base.update(kw)
    return MetaToken(**base)  # type: ignore[arg-type]


def test_load_is_none_before_any_save() -> None:
    assert InMemoryTokenStore().load() is None


def test_save_then_load_roundtrip() -> None:
    store = InMemoryTokenStore()
    tok = _tok()
    store.save(tok)
    assert store.load() == tok


def test_save_overwrites_previous_token() -> None:
    store = InMemoryTokenStore()
    store.save(_tok(access_token="old"))
    store.save(_tok(access_token="new"))
    loaded = store.load()
    assert loaded is not None and loaded.access_token == "new"


def test_clear_removes_token() -> None:
    store = InMemoryTokenStore()
    store.save(_tok())
    store.clear()
    assert store.load() is None


def test_meta_token_is_frozen_and_json_safe() -> None:
    # R-JSON: el DTO que cruza boundaries es frozen + JSON-serializable.
    tok = _tok()
    with pytest.raises(dataclasses.FrozenInstanceError):
        tok.access_token = "mutated"  # type: ignore[misc]
    d = dataclasses.asdict(tok)
    assert d["account_name"] == "Hubara" and list(d["scopes"]) == ["ads_read"]
