"""Tests del enrichment de nombres reales vía Meta Marketing API.

Problema que resuelve (caso real 2026-07-01): el dashboard nombra las
campañas por el `headline` del referral — que es el TEXTO DEL CTA del ad
("Chatea con nosotros"), no el nombre de la campaña en Ads Manager ("Día
del Padre"). Dos ads distintos con el mismo CTA se ven idénticos.

`fetch_meta_ad_names` hace UN GET batch a Graph API
(`/?ids=...&fields=name,campaign{id,name}`) y `enrich_campaign_names`
(pura) reescribe los summaries. Todo best-effort: sin token / error HTTP
→ el dashboard sigue mostrando headlines (nunca bloquea).
"""
from __future__ import annotations

import httpx

from src.plugins.ads.aggregation import AdsCampaignSummary
from src.plugins.ads.meta_names import (
    enrich_campaign_names,
    fetch_meta_ad_names,
)


def _mock_transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _summary(**overrides) -> AdsCampaignSummary:
    base = dict(
        id="120243120899820317",
        name="Chatea con nosotros",
        source_type="ad",
        started=1,
        first_seen_ms=1_714_000_000_000,
        last_seen_ms=1_714_000_000_000,
    )
    base.update(overrides)
    return AdsCampaignSummary(**base)


class TestFetchMetaAdNames:
    def test_parses_batch_response(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert "ids=120243120899820317" in str(request.url)
            assert "fields=" in str(request.url)
            return httpx.Response(
                200,
                json={
                    "120243120899820317": {
                        "id": "120243120899820317",
                        "name": "Ad camiseta mundialista",
                        "campaign": {
                            "id": "CAMP_9",
                            "name": "Día del Padre 2026",
                        },
                    }
                },
            )

        names = fetch_meta_ad_names(
            ["120243120899820317"],
            token="TOK",
            transport=_mock_transport(handler),
        )
        assert names == {
            "120243120899820317": {
                "ad_name": "Ad camiseta mundialista",
                "campaign_name": "Día del Padre 2026",
                "campaign_id": "CAMP_9",
            }
        }

    def test_empty_when_no_token(self):
        assert fetch_meta_ad_names(["1"], token="") == {}

    def test_empty_when_no_ids(self):
        assert fetch_meta_ad_names([], token="TOK") == {}

    def test_empty_on_http_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": {"message": "nope"}})

        assert (
            fetch_meta_ad_names(
                ["1"], token="TOK", transport=_mock_transport(handler)
            )
            == {}
        )

    def test_empty_on_network_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        assert (
            fetch_meta_ad_names(
                ["1"], token="TOK", transport=_mock_transport(handler)
            )
            == {}
        )


class TestEnrichCampaignNames:
    def test_overrides_name_and_keeps_headline_as_creative(self):
        camp = _summary()
        names = {
            "120243120899820317": {
                "ad_name": "Ad camiseta mundialista",
                "campaign_name": "Día del Padre 2026",
                "campaign_id": "CAMP_9",
            }
        }
        out = enrich_campaign_names([camp], names)
        assert out[0].name == "Día del Padre 2026 · Ad camiseta mundialista"
        # el headline original no se pierde — pasa a creative_title
        assert out[0].creative_title == "Chatea con nosotros"
        assert out[0].meta_campaign_id == "CAMP_9"

    def test_untouched_when_id_not_in_names(self):
        camp = _summary()
        out = enrich_campaign_names([camp], {})
        assert out[0] is camp

    def test_direct_bucket_never_enriched(self):
        camp = _summary(id="direct", name="Clientes directos · sin campaña",
                        source_type="direct")
        out = enrich_campaign_names(
            [camp], {"direct": {"ad_name": "X", "campaign_name": "Y",
                                "campaign_id": "Z"}}
        )
        assert out[0] is camp

    def test_campaign_name_alone_when_no_ad_name(self):
        camp = _summary()
        names = {
            "120243120899820317": {
                "ad_name": None,
                "campaign_name": "Día del Padre 2026",
                "campaign_id": "CAMP_9",
            }
        }
        out = enrich_campaign_names([camp], names)
        assert out[0].name == "Día del Padre 2026"
