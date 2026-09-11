"""Cliente del Marketing API (Graph) — port + vendor httpx (respx) + fake."""
from __future__ import annotations

import httpx
import respx

from src.plugins.ads.meta.client import (
    FakeMetaAds,
    GraphMetaAds,
    MetaAdAccount,
    MetaAdsPort,
    MetaCampaignMeta,
)
from src.plugins.ads.meta.parse import MetaCampaignMetrics
from src.sdk.connectorkit import graph_url

_GRAPH = graph_url()

_INSIGHTS = {
    "data": [
        {
            "campaign_id": "120210000111",
            "campaign_name": "Duo zodiacal",
            "spend": "896823",
            "impressions": "45000",
            "reach": "38000",
            "clicks": "571",
            "actions": [
                {
                    "action_type": "onsite_conversion.messaging_conversation_started_7d",
                    "value": "205",
                }
            ],
        }
    ]
}
_ACCOUNTS = {
    "data": [
        {"id": "act_1010393601284112", "name": "Hubara", "currency": "COP", "account_status": 1}
    ]
}
_CAMPAIGNS = {
    "data": [
        {"id": "120210000111", "name": "Duo zodiacal", "status": "ACTIVE", "objective": "OUTCOME_SALES"}
    ]
}


def test_fake_is_a_valid_port_and_serves_canned_data() -> None:
    fake = FakeMetaAds(
        accounts=[MetaAdAccount("act_1", "Hubara", "COP", 1)],
        metrics=[MetaCampaignMetrics("c1", "Duo", 100.0, 10, 8, 5, 2)],
        campaigns=[MetaCampaignMeta("c1", "Duo", "ACTIVE", "OUTCOME_SALES")],
    )
    assert isinstance(fake, MetaAdsPort)
    assert fake.list_ad_accounts("tok")[0].name == "Hubara"
    assert fake.fetch_campaign_metrics("tok", "act_1", since="a", until="b")[0].campaign_id == "c1"
    assert fake.list_campaigns("tok", "act_1")[0].status == "ACTIVE"


@respx.mock
def test_graph_list_ad_accounts() -> None:
    respx.get(f"{_GRAPH}/me/adaccounts").mock(
        return_value=httpx.Response(200, json=_ACCOUNTS)
    )
    accts = GraphMetaAds().list_ad_accounts("TOK")
    assert accts == [MetaAdAccount("act_1010393601284112", "Hubara", "COP", 1)]


@respx.mock
def test_graph_fetch_campaign_metrics_parses_insights() -> None:
    route = respx.get(f"{_GRAPH}/act_1010393601284112/insights").mock(
        return_value=httpx.Response(200, json=_INSIGHTS)
    )
    rows = GraphMetaAds().fetch_campaign_metrics(
        "TOK", "act_1010393601284112", since="2026-06-01", until="2026-06-30"
    )
    assert rows[0].messaging_conversations_started == 205
    assert rows[0].spend == 896823.0
    # el bearer viaja en el header Authorization
    assert route.calls.last.request.headers["authorization"] == "Bearer TOK"


@respx.mock
def test_graph_fetch_adset_metrics_parses_level_adset() -> None:
    """Segmentación (2026-07-10): insights level=adset → métricas por segmento,
    con campaign_id para colgarlo de su campaña."""
    adset_insights = {
        "data": [
            {
                "adset_id": "ADSET_3",
                "adset_name": "Hombres 25-45 Bogotá",
                "campaign_id": "120210000111",
                "spend": "320500",
                "impressions": "15000",
                "reach": "12100",
                "clicks": "210",
                "actions": [
                    {
                        "action_type": "onsite_conversion.messaging_conversation_started_7d",
                        "value": "44",
                    }
                ],
            }
        ]
    }
    route = respx.get(f"{_GRAPH}/act_1010393601284112/insights").mock(
        return_value=httpx.Response(200, json=adset_insights)
    )
    rows = GraphMetaAds().fetch_adset_metrics(
        "TOK", "act_1010393601284112", since="2026-06-01", until="2026-06-30"
    )
    assert rows[0].adset_id == "ADSET_3"
    assert rows[0].campaign_id == "120210000111"
    assert rows[0].messaging_conversations_started == 44
    params = dict(route.calls.last.request.url.params)
    assert params["level"] == "adset"


def test_fake_serves_adset_metrics() -> None:
    from src.plugins.ads.meta.parse import MetaAdsetMetrics

    fake = FakeMetaAds(
        adset_metrics=[
            MetaAdsetMetrics("as1", "Segmento A", "c1", 100.0, 10, 8, 5, 2)
        ]
    )
    rows = fake.fetch_adset_metrics("tok", "act_1", since="a", until="b")
    assert rows[0].adset_id == "as1"


@respx.mock
def test_graph_list_campaigns_returns_status_and_objective() -> None:
    respx.get(f"{_GRAPH}/act_1010393601284112/campaigns").mock(
        return_value=httpx.Response(200, json=_CAMPAIGNS)
    )
    camps = GraphMetaAds().list_campaigns("TOK", "act_1010393601284112")
    assert camps[0] == MetaCampaignMeta("120210000111", "Duo zodiacal", "ACTIVE", "OUTCOME_SALES")


@respx.mock
def test_graph_fetch_raw_insights_returns_pod_shape() -> None:
    # Shape EXACTO que consume el pod ads-analytics: {account_currency, data:[diario]}.
    raw = {
        "data": [
            {
                "date_start": "2026-06-15",
                "date_stop": "2026-06-15",
                "campaign_id": "120238728477970317",
                "campaign_name": "Duo zodiacal",
                "spend": "120000",
                "inline_link_clicks": "80",
                "actions": [
                    {"action_type": "onsite_conversion.messaging_conversation_started_7d", "value": "40"}
                ],
            }
        ]
    }
    route = respx.get(
        f"{_GRAPH}/act_1010393601284112/insights"
    ).mock(return_value=httpx.Response(200, json=raw))
    out = GraphMetaAds().fetch_raw_insights(
        "TOK", "act_1010393601284112", since="2026-06-15", until="2026-06-16", currency="COP"
    )
    assert out["account_currency"] == "COP"
    assert out["data"][0]["campaign_id"] == "120238728477970317"
    # daily rows + actions breakdown
    params = dict(route.calls.last.request.url.params)
    assert params["time_increment"] == "1"
    assert params["action_breakdowns"] == "action_type"


@respx.mock
def test_graph_update_campaign_status_posts_status_with_bearer() -> None:
    route = respx.post(f"{_GRAPH}/120210000111").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    ok = GraphMetaAds().update_campaign_status("TOK", "120210000111", "PAUSED")
    assert ok is True
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer TOK"
    assert b"status=PAUSED" in req.content


@respx.mock
def test_graph_update_campaign_status_ambiguous_response_is_false() -> None:
    # Respuesta 2xx SIN `success: true` → no reportamos éxito (premortem #4).
    respx.post(f"{_GRAPH}/120210000111").mock(
        return_value=httpx.Response(200, json={"id": "120210000111"})
    )
    assert GraphMetaAds().update_campaign_status("TOK", "120210000111", "PAUSED") is False


def test_fake_records_status_changes_for_assertion() -> None:
    fake = FakeMetaAds()
    assert fake.update_campaign_status("tok", "c1", "PAUSED") is True
    assert fake.status_changes == [("c1", "PAUSED")]


# ── Creativos por segmento (2026-09-10): insights level=ad + creativo ────────


@respx.mock
def test_graph_fetch_ad_metrics_uses_level_ad() -> None:
    ad_insights = {
        "data": [
            {
                "ad_id": "AD_7",
                "ad_name": "Video velas",
                "adset_id": "ADSET_3",
                "campaign_id": "120210000111",
                "spend": "120500",
                "impressions": "8000",
                "reach": "6100",
                "clicks": "95",
                "actions": [
                    {
                        "action_type": "onsite_conversion.messaging_conversation_started_7d",
                        "value": "12",
                    }
                ],
            }
        ]
    }
    route = respx.get(f"{_GRAPH}/act_1010393601284112/insights").mock(
        return_value=httpx.Response(200, json=ad_insights)
    )
    rows = GraphMetaAds().fetch_ad_metrics(
        "TOK", "act_1010393601284112", since="2026-06-01", until="2026-06-30"
    )
    assert rows[0].ad_id == "AD_7"
    assert rows[0].adset_id == "ADSET_3"
    assert rows[0].messaging_conversations_started == 12
    params = dict(route.calls.last.request.url.params)
    assert params["level"] == "ad"
    assert "ad_id" in params["fields"] and "adset_id" in params["fields"]


@respx.mock
def test_graph_fetch_ad_metrics_follows_paging() -> None:
    """Meta pagina los insights (cursor `paging.next`). Con más anuncios que
    `limit`, el vendor sigue el cursor hasta agotar — nunca deja anuncios
    afuera silenciosamente."""
    page1 = {
        "data": [{"ad_id": "AD_1", "ad_name": "a", "adset_id": "S", "campaign_id": "C",
                  "spend": "1", "impressions": "1", "reach": "1", "clicks": "1"}],
        "paging": {"next": f"{_GRAPH}/act_1010393601284112/insights?after=CURSOR"},
    }
    page2 = {
        "data": [{"ad_id": "AD_2", "ad_name": "b", "adset_id": "S", "campaign_id": "C",
                  "spend": "2", "impressions": "2", "reach": "2", "clicks": "2"}],
    }
    route = respx.get(f"{_GRAPH}/act_1010393601284112/insights")
    route.side_effect = [httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
    rows = GraphMetaAds().fetch_ad_metrics(
        "TOK", "act_1010393601284112", since="2026-06-01", until="2026-06-30"
    )
    assert [r.ad_id for r in rows] == ["AD_1", "AD_2"]
    assert route.call_count == 2


def test_fake_serves_ad_metrics() -> None:
    from src.plugins.ads.meta.parse import MetaAdMetrics

    fake = FakeMetaAds(
        ad_metrics=[MetaAdMetrics("ad1", "Anuncio", "as1", "c1", 100.0, 10, 8, 5, 2)]
    )
    rows = fake.fetch_ad_metrics("tok", "act_1", since="a", until="b")
    assert rows[0].ad_id == "ad1"


@respx.mock
def test_graph_fetch_ad_creative_returns_big_thumbnail_and_preview() -> None:
    """El inspector necesita el creativo grande (thumbnail_width/height en el
    edge `adcreatives`) + la vista previa real (`/previews` devuelve el iframe)."""
    creatives = respx.get(f"{_GRAPH}/AD_7/adcreatives").mock(
        return_value=httpx.Response(200, json={
            "data": [{
                "id": "CR_1",
                "thumbnail_url": "https://cdn.fb/big.jpg",
                "image_url": "https://cdn.fb/full.jpg",
                "body": "Velas que iluminan",
                "title": "Compra hoy",
                "call_to_action_type": "WHATSAPP_MESSAGE",
            }]
        })
    )
    previews = respx.get(f"{_GRAPH}/AD_7/previews").mock(
        return_value=httpx.Response(200, json={
            "data": [{"body": "<iframe src=\"https://www.facebook.com/ads/api/preview_iframe.php?d=abc\"></iframe>"}]
        })
    )
    creative = GraphMetaAds().fetch_ad_creative("TOK", "AD_7")
    assert creative is not None
    assert creative.ad_id == "AD_7"
    assert creative.thumbnail_url == "https://cdn.fb/big.jpg"
    assert creative.image_url == "https://cdn.fb/full.jpg"
    assert creative.body == "Velas que iluminan"
    assert creative.title == "Compra hoy"
    assert creative.call_to_action == "WHATSAPP_MESSAGE"
    assert creative.preview_html.startswith("<iframe")
    cparams = dict(creatives.calls.last.request.url.params)
    assert cparams["thumbnail_width"] == "600" and cparams["thumbnail_height"] == "600"
    pparams = dict(previews.calls.last.request.url.params)
    assert pparams["ad_format"] == "MOBILE_FEED_STANDARD"


@respx.mock
def test_graph_fetch_ad_creative_survives_preview_failure() -> None:
    """La vista previa es best-effort: si `/previews` falla, el creativo
    igual vuelve (thumbnail + textos) con `preview_html=None`."""
    respx.get(f"{_GRAPH}/AD_7/adcreatives").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "CR_1", "thumbnail_url": "https://cdn.fb/big.jpg"}]})
    )
    respx.get(f"{_GRAPH}/AD_7/previews").mock(return_value=httpx.Response(500, json={}))
    creative = GraphMetaAds().fetch_ad_creative("TOK", "AD_7")
    assert creative is not None
    assert creative.thumbnail_url == "https://cdn.fb/big.jpg"
    assert creative.preview_html is None


def test_fake_serves_ad_creative() -> None:
    from src.plugins.ads.meta.client import MetaAdCreative

    fake = FakeMetaAds(
        creatives={"AD_7": MetaAdCreative("AD_7", "https://t", None, "b", "t", "CTA", "<iframe></iframe>")}
    )
    assert fake.fetch_ad_creative("tok", "AD_7").title == "t"
    assert fake.fetch_ad_creative("tok", "NOPE") is None
