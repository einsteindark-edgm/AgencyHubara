"""Catálogo de tools: cada tool ↔ su `GET /api/ads/*` con la query exacta del dashboard.

Fuente de verdad: `src/shared/resources.ts` (RESOURCES) del plugin `@acktos/dsh-hubara`.
"""

import pytest

from hubara_gateway.catalog import TOOLS, ArgumentError, resolve_request

C1 = "120247398115530317"
A1 = "120247398115600317"


def test_catalog_exposes_exactly_the_plugin_tools() -> None:
    assert set(TOOLS) == {
        "ads_get_campaigns",
        "ads_get_adsets",
        "ads_get_ads",
        "ads_get_conversations",
        "ads_get_daily",
        "ads_get_creative",
        "ads_get_agent_referrals",
        "ads_get_meta_status",
        "ads_get_analysis_agents",
        "ads_get_analysis_runs",
        "ads_get_analysis_run",
    }


@pytest.mark.parametrize(
    ("tool", "args", "path", "query"),
    [
        ("ads_get_campaigns", {}, "/api/ads/campaigns", {"days": "30"}),
        ("ads_get_campaigns", {"window": "total"}, "/api/ads/campaigns", {}),
        ("ads_get_adsets", {"campaign_id": C1, "window": "7d"}, f"/api/ads/campaigns/{C1}/adsets", {"days": "7"}),
        (
            "ads_get_ads",
            {"campaign_id": C1, "adset_id": A1, "window": "2026-09-01..2026-09-30"},
            f"/api/ads/campaigns/{C1}/adsets/{A1}/ads",
            {"from": "2026-09-01", "to": "2026-09-30"},
        ),
        (
            "ads_get_conversations",
            {"campaign_id": C1, "adset_id": A1, "window": "30d"},
            f"/api/ads/campaigns/{C1}/conversations",
            {"days": "30", "adset_id": A1},
        ),
        ("ads_get_conversations", {"campaign_id": "direct"}, "/api/ads/campaigns/direct/conversations", {"days": "30"}),
        ("ads_get_daily", {"campaign_id": C1, "window": "total"}, f"/api/ads/campaigns/{C1}/daily", {"days": "90"}),
        (
            "ads_get_daily",
            {"campaign_id": C1, "adset_id": A1, "window": "7d"},
            f"/api/ads/campaigns/{C1}/daily",
            {"days": "7", "adset_id": A1},
        ),
        ("ads_get_creative", {"ad_id": "120247398115620317"}, "/api/ads/ads/120247398115620317/creative", {}),
        ("ads_get_agent_referrals", {"window": "90d"}, "/api/ads/agent-referrals", {"days": "90"}),
        ("ads_get_meta_status", {}, "/api/ads/meta/status", {}),
        ("ads_get_analysis_agents", {}, "/api/ads/analysis/agents", {}),
        ("ads_get_analysis_runs", {}, "/api/ads/analysis/runs", {"limit": "20"}),
        (
            "ads_get_analysis_runs",
            {"campaign_id": C1, "limit": "5"},
            "/api/ads/analysis/runs",
            {"limit": "5", "campaign_id": C1},
        ),
        ("ads_get_analysis_run", {"run_id": "run-143a22615046"}, "/api/ads/analysis/runs/run-143a22615046", {}),
    ],
)
def test_resolve_request_maps_tool_to_backend_get(tool: str, args: dict, path: str, query: dict) -> None:
    assert resolve_request(tool, args) == (path, query)



@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("ads_get_adsets", {}),
        ("ads_get_ads", {"campaign_id": C1}),
        ("ads_get_creative", {"ad_id": ""}),
        ("ads_get_analysis_run", {}),
    ],
)
def test_resolve_request_missing_required_raises(tool: str, args: dict) -> None:
    with pytest.raises(ArgumentError, match="missing parameter"):
        resolve_request(tool, args)


def test_resolve_request_invalid_window_raises() -> None:
    with pytest.raises(ArgumentError, match="invalid window"):
        resolve_request("ads_get_campaigns", {"window": "14d"})


@pytest.mark.parametrize("limit", ["abc", "1.5", ""])
def test_resolve_request_non_integer_limit_raises(limit: str) -> None:
    if limit == "":
        # vacío = no enviado → default del plugin
        assert resolve_request("ads_get_analysis_runs", {"limit": limit})[1] == {"limit": "20"}
        return
    with pytest.raises(ArgumentError, match="limit"):
        resolve_request("ads_get_analysis_runs", {"limit": limit})


def test_resolve_request_out_of_range_limit_passes_through_to_backend_clamp() -> None:
    # El backend recorta a 1..100 sin 422; la fuente REST del plugin ve lo mismo.
    assert resolve_request("ads_get_analysis_runs", {"limit": "500"})[1] == {"limit": "500"}


@pytest.mark.parametrize("bad", ["..", ".", "../meta", "a b", "x\ny", "%2e%2e", "c1/../../chats"])
def test_resolve_request_rejects_ids_that_could_escape_the_route(bad: str) -> None:
    with pytest.raises(ArgumentError, match="invalid parameter"):
        resolve_request("ads_get_ads", {"campaign_id": "c1", "adset_id": bad})
    with pytest.raises(ArgumentError, match="invalid parameter"):
        resolve_request("ads_get_conversations", {"campaign_id": "c1", "adset_id": bad})
