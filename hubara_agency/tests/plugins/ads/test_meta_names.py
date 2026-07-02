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
                        "effective_status": "ACTIVE",
                        "campaign": {
                            "id": "CAMP_9",
                            "name": "Día del Padre 2026",
                            "objective": "OUTCOME_SALES",
                            "start_time": "2026-06-20T08:00:00-0500",
                        },
                        "adset": {"name": "Papás 25-45 Bogotá"},
                        "insights": {
                            "data": [
                                {
                                    "spend": "123456.78",
                                    "impressions": "9871",
                                    "reach": "8100",
                                    "clicks": "230",
                                }
                            ]
                        },
                    }
                },
            )

        names = fetch_meta_ad_names(
            ["120243120899820317"],
            token="TOK",
            transport=_mock_transport(handler),
        )
        info = names["120243120899820317"]
        assert info["ad_name"] == "Ad camiseta mundialista"
        assert info["campaign_name"] == "Día del Padre 2026"
        assert info["campaign_id"] == "CAMP_9"
        # Metadata Meta (fix métricas 2026-07-01)
        assert info["status"] == "active"        # effective_status mapeado
        assert info["objective"] == "Sales"      # OUTCOME_SALES → legible
        assert info["ad_set"] == "Papás 25-45 Bogotá"
        assert info["campaign_start_time"] == "2026-06-20T08:00:00-0500"
        # Insights lifetime (spend llega como string en Graph → numérico)
        assert info["spend"] == 123456.78
        assert info["impressions"] == 9871
        assert info["reach"] == 8100
        assert info["clicks"] == 230

    def test_parses_minimal_node_without_insights(self):
        """Nodo sin insights/adset (ad recién creado) → campos en None,
        sin crashear."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"1": {"id": "1", "name": "Ad X"}},
            )

        info = fetch_meta_ad_names(
            ["1"], token="TOK", transport=_mock_transport(handler)
        )["1"]
        assert info["ad_name"] == "Ad X"
        assert info["spend"] is None
        assert info["status"] is None
        assert info["ad_set"] is None

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


class TestEnrichFillsMetrics:
    def test_fills_meta_metrics_and_days_run(self):
        camp = _summary()
        names = {
            "120243120899820317": {
                "ad_name": "Ad camiseta",
                "campaign_name": "Día del Padre 2026",
                "campaign_id": "CAMP_9",
                "status": "active",
                "objective": "Sales",
                "ad_set": "Papás 25-45",
                "campaign_start_time": "2026-06-20T08:00:00-0500",
                "spend": 123456.78,
                "impressions": 9871,
                "reach": 8100,
                "clicks": 230,
            }
        }
        # now = 2026-07-01T00:00:00Z → 10 días corridos desde el start 06-20
        now_ms = 1_782_864_000_000
        out = enrich_campaign_names([camp], names, now_ms=now_ms)
        c = out[0]
        assert c.status == "active"
        assert c.objective == "Sales"
        assert c.ad_set == "Papás 25-45"
        assert c.spend == 123456.78
        assert c.impressions == 9871
        assert c.reach == 8100
        assert c.clicks == 230
        assert c.days_run == 10

    def test_partial_info_keeps_vault_values(self):
        """Info de Meta sin métricas (solo nombre) no pisa nada con None."""
        camp = _summary(spend=None, status=None)
        names = {
            "120243120899820317": {
                "ad_name": "Ad camiseta",
                "campaign_name": None,
                "campaign_id": None,
                "status": None,
                "objective": None,
                "ad_set": None,
                "campaign_start_time": None,
                "spend": None,
                "impressions": None,
                "reach": None,
                "clicks": None,
            }
        }
        out = enrich_campaign_names([camp], names, now_ms=1_782_864_000_000)
        assert out[0].name == "Ad camiseta"
        assert out[0].spend is None
        assert out[0].days_run is None
