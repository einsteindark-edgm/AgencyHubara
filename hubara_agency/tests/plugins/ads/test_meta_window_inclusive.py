"""Guard 2026-11-05: a custom range asked Meta for one day too many.

`_window` returns an EXCLUSIVE `until_ms` (Bogotá midnight of the day after
`to`), but Meta's insights `until` is INCLUSIVE. `date.fromtimestamp(until_ms)`
turned "October 1–31" into `until=2026-11-01`: October showed November 1st's
ad spend too (seen comparing the Costs tab against Meta's own account total).
"""
from __future__ import annotations

import pytest

import src.plugins.ads.api as ads_mod
from src.plugins.ads.meta.client import FakeMetaAds
from src.plugins.ads.meta.token_store import InMemoryTokenStore, MetaToken


class RecordingMetaAds(FakeMetaAds):
    def __init__(self) -> None:
        super().__init__()
        self.windows: list[tuple[str, str]] = []

    def fetch_campaign_metrics(self, token, account_id, *, since, until):
        self.windows.append((since, until))
        return super().fetch_campaign_metrics(token, account_id, since=since, until=until)

    def fetch_adset_metrics(self, token, account_id, *, since, until):
        self.windows.append((since, until))
        return super().fetch_adset_metrics(token, account_id, since=since, until=until)

    def fetch_ad_metrics(self, token, account_id, *, since, until):
        self.windows.append((since, until))
        return super().fetch_ad_metrics(token, account_id, since=since, until=until)


@pytest.fixture
def meta(monkeypatch):
    store = InMemoryTokenStore()
    store.save(MetaToken("EAA", None, ("ads_read",), "act_1", "Hubara"))
    ads = RecordingMetaAds()
    monkeypatch.setattr(ads_mod, "_meta_store", lambda: store)
    monkeypatch.setattr(ads_mod, "_meta_ads", lambda: ads)
    caches = (ads_mod._meta_campaign_cache, ads_mod._meta_adset_cache, ads_mod._meta_ad_cache)
    for cache in caches:
        cache.clear()
    yield ads
    for cache in caches:
        cache.clear()


@pytest.mark.parametrize(
    "fetch", ["_cached_meta_campaigns", "_cached_meta_adsets", "_cached_meta_ads"]
)
def test_custom_range_asks_meta_exactly_the_chosen_days(meta, fetch) -> None:
    since_ms, until_ms = ads_mod._window(None, "2026-10-01", "2026-10-31")

    getattr(ads_mod, fetch)(since_ms, until_ms)

    assert meta.windows == [("2026-10-01", "2026-10-31")]
