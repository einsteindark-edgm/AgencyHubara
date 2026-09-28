"""El cache de nombres de anuncios del dashboard de chats queda acotado.

Mismo patrón que la fuga de la API de Ads (incidente 2026-09-25): la key es el
set de ad ids de las conversaciones listadas, así que cada chat nuevo que entra
por un anuncio crea una entrada nueva; el TTL solo se miraba al leer.
"""
from __future__ import annotations

import pytest

from src.plugins.chats.api import dashboard


@pytest.fixture
def fetches(monkeypatch):
    calls: list[list[str]] = []

    def fake_fetch(ad_ids, *, token, transport=None):
        calls.append(sorted(ad_ids))
        return {}

    monkeypatch.setattr(dashboard, "meta_marketing_token", lambda: "TOK")
    monkeypatch.setattr(dashboard, "fetch_meta_ad_names", fake_fetch)
    dashboard._ad_names_cache.clear()
    yield calls
    dashboard._ad_names_cache.clear()


def test_ad_names_cache_stays_bounded_across_many_ad_sets(fetches):
    for n in range(40):
        dashboard._resolve_ad_names([f"AD_{i}" for i in range(n + 1)])

    assert len(dashboard._ad_names_cache) <= 16


def test_ad_names_same_set_is_fetched_once(fetches):
    dashboard._resolve_ad_names(["AD_2", "AD_1"])
    dashboard._resolve_ad_names(["AD_1", "AD_2"])

    assert fetches == [["AD_1", "AD_2"]]
