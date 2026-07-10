"""Parser PURO de insights del Graph API (`/act_<id>/insights?level=campaign`).

A diferencia del MCP (strings humanos `"$ 896.823 COP"`), el Graph devuelve
números limpios como strings (`"896823"`, `"571"`) + el array `actions`. El
parser los normaliza a métricas tipadas para poblar el dashboard. Golden-tested.
"""
from __future__ import annotations

from src.plugins.ads.meta.parse import parse_campaign_insights

# Forma real del Graph /insights (level=campaign). Campaña CTWA en COP.
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
                },
                {"action_type": "link_click", "value": "571"},
            ],
            "date_start": "2026-06-01",
            "date_stop": "2026-06-30",
        }
    ],
    "paging": {"cursors": {"before": "x", "after": "y"}},
}


def test_parses_one_campaign_row() -> None:
    rows = parse_campaign_insights(_INSIGHTS)
    assert len(rows) == 1


def test_normalizes_numeric_fields() -> None:
    row = parse_campaign_insights(_INSIGHTS)[0]
    assert row.campaign_id == "120210000111"
    assert row.campaign_name == "Duo zodiacal"
    assert row.spend == 896823.0
    assert row.impressions == 45000
    assert row.reach == 38000
    assert row.clicks == 571


def test_extracts_messaging_conversations_from_actions() -> None:
    row = parse_campaign_insights(_INSIGHTS)[0]
    # la señal CTWA vive en actions[onsite_conversion.messaging_conversation_started_7d]
    assert row.messaging_conversations_started == 205


def test_missing_actions_yields_zero_conversations() -> None:
    payload = {"data": [{"campaign_id": "1", "campaign_name": "n", "spend": "0",
                         "impressions": "0", "reach": "0", "clicks": "0"}]}
    row = parse_campaign_insights(payload)[0]
    assert row.messaging_conversations_started == 0


def test_empty_data_yields_empty_list() -> None:
    assert parse_campaign_insights({"data": []}) == []


def test_parses_adset_insights_row() -> None:
    """Segmentación (2026-07-10): level=adset trae adset_id/adset_name +
    campaign_id (para colgar el segmento de su campaña) y las mismas métricas."""
    from src.plugins.ads.meta.parse import parse_adset_insights

    payload = {
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
                "date_start": "2026-06-01",
                "date_stop": "2026-06-30",
            }
        ]
    }
    rows = parse_adset_insights(payload)
    assert len(rows) == 1
    row = rows[0]
    assert row.adset_id == "ADSET_3"
    assert row.adset_name == "Hombres 25-45 Bogotá"
    assert row.campaign_id == "120210000111"
    assert row.spend == 320500.0
    assert row.impressions == 15000
    assert row.reach == 12100
    assert row.clicks == 210
    assert row.messaging_conversations_started == 44


def test_non_numeric_field_degrades_to_zero_not_crash() -> None:
    # Un valor inesperado del boundary externo NO debe tumbar el parseo (premortem #6).
    payload = {"data": [{"campaign_id": "1", "campaign_name": "n", "spend": "N/A",
                         "impressions": "1000", "reach": "", "clicks": "5"}]}
    row = parse_campaign_insights(payload)[0]
    assert row.spend == 0.0
    assert row.impressions == 1000
    assert row.clicks == 5
