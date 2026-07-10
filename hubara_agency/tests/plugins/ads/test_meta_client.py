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
    respx.get("https://graph.facebook.com/v25.0/me/adaccounts").mock(
        return_value=httpx.Response(200, json=_ACCOUNTS)
    )
    accts = GraphMetaAds().list_ad_accounts("TOK")
    assert accts == [MetaAdAccount("act_1010393601284112", "Hubara", "COP", 1)]


@respx.mock
def test_graph_fetch_campaign_metrics_parses_insights() -> None:
    route = respx.get("https://graph.facebook.com/v25.0/act_1010393601284112/insights").mock(
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
    route = respx.get("https://graph.facebook.com/v25.0/act_1010393601284112/insights").mock(
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
    respx.get("https://graph.facebook.com/v25.0/act_1010393601284112/campaigns").mock(
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
        "https://graph.facebook.com/v25.0/act_1010393601284112/insights"
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
    route = respx.post("https://graph.facebook.com/v25.0/120210000111").mock(
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
    respx.post("https://graph.facebook.com/v25.0/120210000111").mock(
        return_value=httpx.Response(200, json={"id": "120210000111"})
    )
    assert GraphMetaAds().update_campaign_status("TOK", "120210000111", "PAUSED") is False


def test_fake_records_status_changes_for_assertion() -> None:
    fake = FakeMetaAds()
    assert fake.update_campaign_status("tok", "c1", "PAUSED") is True
    assert fake.status_changes == [("c1", "PAUSED")]
