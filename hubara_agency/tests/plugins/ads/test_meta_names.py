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
                "adset_id": None,
                "adset_name": None,
                "thumbnail_url": None,
            }
        }

    def test_parses_adset_from_batch_response(self):
        """Segmentación (2026-07-10): el batch pide adset{id,name} y el
        resolver lo expone — es el eslabón ad→segmento de toda la feature."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert "adset{id,name}" in request.url.params["fields"]
            return httpx.Response(
                200,
                json={
                    "AD_1": {
                        "id": "AD_1",
                        "name": "Ad camiseta",
                        "campaign": {"id": "CAMP_9", "name": "Día del Padre"},
                        "adset": {"id": "ADSET_3", "name": "Hombres 25-45 Bogotá"},
                    }
                },
            )

        names = fetch_meta_ad_names(
            ["AD_1"], token="TOK", transport=_mock_transport(handler)
        )
        assert names["AD_1"]["adset_id"] == "ADSET_3"
        assert names["AD_1"]["adset_name"] == "Hombres 25-45 Bogotá"

    def test_adset_none_when_absent(self):
        """Ads sin adset en la respuesta (post orgánico / nodo raro) degradan
        a None — el caller agrupa como 'sin segmento', nunca explota."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"AD_1": {"id": "AD_1", "name": "Ad X"}},
            )

        names = fetch_meta_ad_names(
            ["AD_1"], token="TOK", transport=_mock_transport(handler)
        )
        assert names["AD_1"]["adset_id"] is None
        assert names["AD_1"]["adset_name"] is None

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
                "thumbnail_url": None,
            }
        }
        out = enrich_campaign_names([camp], names)
        assert out[0].name == "Día del Padre 2026 · Ad camiseta mundialista"
        # el headline original no se pierde — pasa a creative_title
        assert out[0].creative_title == "Chatea con nosotros"
        assert out[0].meta_campaign_id == "CAMP_9"

    def test_fills_adset_on_summary(self):
        """Segmentación (2026-07-10): el enrichment baja el segmento al
        summary — `ad_set` (nombre, para el inspector) + `meta_adset_id`
        (para agrupar el drill-down por segmento)."""
        camp = _summary()
        names = {
            "120243120899820317": {
                "ad_name": "Ad camiseta",
                "campaign_name": "Día del Padre 2026",
                "campaign_id": "CAMP_9",
                "adset_id": "ADSET_3",
                "adset_name": "Hombres 25-45 Bogotá",
                "thumbnail_url": None,
            }
        }
        out = enrich_campaign_names([camp], names)
        assert out[0].ad_set == "Hombres 25-45 Bogotá"
        assert out[0].meta_adset_id == "ADSET_3"

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


def test_fetch_carries_creative_thumbnail(respx_or_transport=None):
    """El creativo real del inspector (2026-07-09): el batch trae
    creative{thumbnail_url} y el enrichment lo baja al summary."""
    import httpx

    from src.plugins.ads.aggregation import AdsCampaignSummary
    from src.plugins.ads.meta_names import enrich_campaign_names, fetch_meta_ad_names

    def handler(request: httpx.Request) -> httpx.Response:
        assert "creative{thumbnail_url}" in request.url.params["fields"]
        return httpx.Response(200, json={
            "AD_1": {
                "id": "AD_1",
                "name": "Ad Padre",
                "campaign": {"id": "c-1", "name": "Día del padre"},
                "creative": {"thumbnail_url": "https://cdn.fb/thumb.jpg"},
            }
        })

    names = fetch_meta_ad_names(
        ["AD_1"], token="T", transport=httpx.MockTransport(handler)
    )
    assert names["AD_1"]["thumbnail_url"] == "https://cdn.fb/thumb.jpg"

    camp = AdsCampaignSummary(
        id="AD_1", name="Chatea", source_type="ad", started=1,
        first_seen_ms=1, last_seen_ms=2,
    )
    enriched = enrich_campaign_names([camp], names)[0]
    assert enriched.creative_thumbnail_url == "https://cdn.fb/thumb.jpg"
