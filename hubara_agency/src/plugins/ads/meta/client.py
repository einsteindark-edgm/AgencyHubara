"""Cliente del Marketing API de Meta (Graph) — port + vendor httpx + fake.

`MetaAdsPort` abstrae el acceso de lectura a Meta (R-DIP). Vendor real:
`GraphMetaAds` (httpx sync, import perezoso para el gate de lazy surface). Fake:
`FakeMetaAds` (datos canned para tests/composición). El bearer del usuario se
pasa por método (lo provee el token store). Escrituras (gestión) en Phase 3.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from src.plugins.ads.meta.parse import (
    MetaAdsetMetrics,
    MetaCampaignMetrics,
    parse_adset_insights,
    parse_campaign_insights,
)

GRAPH_VERSION = "v25.0"
GRAPH_BASE = "https://graph.facebook.com"
_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class MetaAdAccount:
    account_id: str  # con prefijo "act_"
    name: str
    currency: str
    account_status: int  # 1 = ACTIVE


@dataclass(frozen=True)
class MetaCampaignMeta:
    campaign_id: str
    name: str
    status: str  # ACTIVE | PAUSED | ...
    objective: str


@runtime_checkable
class MetaAdsPort(Protocol):
    def list_ad_accounts(self, token: str) -> list[MetaAdAccount]: ...
    def fetch_campaign_metrics(
        self, token: str, account_id: str, *, since: str, until: str
    ) -> list[MetaCampaignMetrics]: ...
    def fetch_adset_metrics(
        self, token: str, account_id: str, *, since: str, until: str
    ) -> list[MetaAdsetMetrics]: ...
    def list_campaigns(self, token: str, account_id: str) -> list[MetaCampaignMeta]: ...
    def update_campaign_status(self, token: str, campaign_id: str, status: str) -> bool: ...
    def fetch_raw_insights(
        self, token: str, account_id: str, *, since: str, until: str, currency: str
    ) -> dict: ...


class GraphMetaAds:
    """Vendor real: pega al Graph API con el bearer del usuario. Lee solamente."""

    def __init__(self, base_url: str = GRAPH_BASE, api_version: str = GRAPH_VERSION) -> None:
        self._base = base_url.rstrip("/")
        self._v = api_version

    def _get(self, token: str, path: str, params: dict) -> dict:
        import httpx

        url = f"{self._base}/{self._v}/{path.lstrip('/')}"
        with httpx.Client(timeout=_TIMEOUT_S) as client:
            resp = client.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        return resp.json()

    def _post(self, token: str, path: str, data: dict) -> dict:
        import httpx

        url = f"{self._base}/{self._v}/{path.lstrip('/')}"
        with httpx.Client(timeout=_TIMEOUT_S) as client:
            resp = client.post(url, data=data, headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        return resp.json()

    def list_ad_accounts(self, token: str) -> list[MetaAdAccount]:
        data = self._get(token, "me/adaccounts", {"fields": "id,name,currency,account_status"})
        return [
            MetaAdAccount(
                account_id=str(a.get("id", "")),
                name=str(a.get("name", "")),
                currency=str(a.get("currency", "")),
                account_status=int(a.get("account_status", 0) or 0),
            )
            for a in data.get("data", [])
        ]

    def fetch_campaign_metrics(
        self, token: str, account_id: str, *, since: str, until: str
    ) -> list[MetaCampaignMetrics]:
        params = {
            "level": "campaign",
            "fields": "campaign_id,campaign_name,spend,impressions,reach,clicks,actions",
            "time_range": json.dumps({"since": since, "until": until}),
            "limit": "500",
        }
        data = self._get(token, f"{account_id}/insights", params)
        return parse_campaign_insights(data)

    def fetch_adset_metrics(
        self, token: str, account_id: str, *, since: str, until: str
    ) -> list[MetaAdsetMetrics]:
        """Insights level=adset — métricas por segmento (drill-down de campaña)."""
        params = {
            "level": "adset",
            "fields": "adset_id,adset_name,campaign_id,spend,impressions,reach,clicks,actions",
            "time_range": json.dumps({"since": since, "until": until}),
            "limit": "500",
        }
        data = self._get(token, f"{account_id}/insights", params)
        return parse_adset_insights(data)

    def list_campaigns(self, token: str, account_id: str) -> list[MetaCampaignMeta]:
        data = self._get(
            token, f"{account_id}/campaigns", {"fields": "id,name,status,objective", "limit": "500"}
        )
        return [
            MetaCampaignMeta(
                campaign_id=str(c.get("id", "")),
                name=str(c.get("name", "")),
                status=str(c.get("status", "")),
                objective=str(c.get("objective", "")),
            )
            for c in data.get("data", [])
        ]

    def update_campaign_status(self, token: str, campaign_id: str, status: str) -> bool:
        """Pausa/activa una campaña (POST /{campaign_id} con `status`). Idempotente
        en Meta (setear el mismo status no daña). Acción outward → gated por HITL en la UI."""
        data = self._post(token, campaign_id, {"status": status})
        # Meta confirma con {"success": true}. Respuesta ambigua (sin success) → False:
        # no reportamos éxito sin confirmación explícita (premortem #4).
        return data.get("success") is True

    def fetch_raw_insights(
        self, token: str, account_id: str, *, since: str, until: str, currency: str
    ) -> dict:
        """Insights diarios CRUDOS en el shape que consume el pod `ads-analytics`
        (`{account_currency, data:[{date_start, campaign_id, spend, inline_link_clicks,
        actions}]}`). `time_increment=1` = una fila por día; `action_breakdowns=action_type`
        trae la conversación CTWA en `actions`. (Una página, limit 500 — paginación si crece.)"""
        params = {
            "level": "campaign",
            "fields": "campaign_id,campaign_name,spend,inline_link_clicks,actions",
            "time_increment": "1",
            "action_breakdowns": "action_type",
            "time_range": json.dumps({"since": since, "until": until}),
            "limit": "500",
        }
        data = self._get(token, f"{account_id}/insights", params)
        return {"account_currency": currency, "data": data.get("data", [])}


class FakeMetaAds:
    """Fake del `MetaAdsPort` con datos canned (tests + composición sin red)."""

    def __init__(
        self,
        accounts: list[MetaAdAccount] | None = None,
        metrics: list[MetaCampaignMetrics] | None = None,
        campaigns: list[MetaCampaignMeta] | None = None,
        raw_insights: dict | None = None,
        adset_metrics: list[MetaAdsetMetrics] | None = None,
    ) -> None:
        self._accounts = accounts or []
        self._metrics = metrics or []
        self._campaigns = campaigns or []
        self._raw_insights = raw_insights or {"account_currency": "COP", "data": []}
        self._adset_metrics = adset_metrics or []
        self.status_changes: list[tuple[str, str]] = []

    def list_ad_accounts(self, token: str) -> list[MetaAdAccount]:
        return list(self._accounts)

    def fetch_campaign_metrics(
        self, token: str, account_id: str, *, since: str, until: str
    ) -> list[MetaCampaignMetrics]:
        return list(self._metrics)

    def fetch_adset_metrics(
        self, token: str, account_id: str, *, since: str, until: str
    ) -> list[MetaAdsetMetrics]:
        return list(self._adset_metrics)

    def list_campaigns(self, token: str, account_id: str) -> list[MetaCampaignMeta]:
        return list(self._campaigns)

    def update_campaign_status(self, token: str, campaign_id: str, status: str) -> bool:
        self.status_changes.append((campaign_id, status))
        return True

    def fetch_raw_insights(
        self, token: str, account_id: str, *, since: str, until: str, currency: str
    ) -> dict:
        return self._raw_insights
