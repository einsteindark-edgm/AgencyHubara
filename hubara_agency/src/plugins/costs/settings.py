"""Settings of the Costs tab: secrets from the environment, knobs from costs.yaml.

DeepSeek, Jev and Gemini come from the costs each conversation already records
in the vault (no credential). WhatsApp asks Meta and Temporal asks Temporal
Cloud: a missing or placeholder secret leaves that card *not connected* — its
adapter never calls anyone and the card says how to connect it.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from src.plugins.costs.providers.base import CostProvider
from src.plugins.costs.providers.campaigns import AdsCampaignsReader, CampaignsProvider
from src.plugins.costs.providers.conversations import (
    ConversationCostsReader,
    conversation_providers,
)
from src.plugins.costs.providers.temporal import TemporalPrices, TemporalProvider
from src.plugins.costs.providers.whatsapp import WhatsAppProvider
from src.sdk.runtime import WORKSPACE_VAULT_DIR, is_placeholder

CONFIG_PATH = Path(__file__).parent / "config" / "costs.yaml"


def load_config(path: Path = CONFIG_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _secret(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    return None if is_placeholder(value) else str(value).strip()


@dataclass(frozen=True)
class CostsSettings:
    timeout_s: float
    live_ttl_s: float
    closed_ttl_s: float
    fx_fallback_cop_per_usd: Decimal
    fx_per_usd: dict[str, Decimal]
    vault_dir: Path
    meta_ads_currency: str
    whatsapp_token: str | None
    whatsapp_waba_id: str | None
    temporal_api_key: str | None
    temporal_namespace: str | None
    temporal_prices: TemporalPrices

    @classmethod
    def from_env(cls, env: Mapping[str, str], config: dict) -> CostsSettings:
        cache = config.get("cache") or {}
        fx = config.get("fx") or {}
        temporal = (config.get("providers") or {}).get("temporal") or {}
        return cls(
            timeout_s=float(config.get("timeout_seconds", 25)),
            live_ttl_s=float(cache.get("live_ttl_seconds", 600)),
            closed_ttl_s=float(cache.get("closed_ttl_seconds", 21600)),
            fx_fallback_cop_per_usd=Decimal(str(fx.get("cop_per_usd_fallback", "4000"))),
            fx_per_usd={
                str(cur).upper(): Decimal(str(rate))
                for cur, rate in (fx.get("per_usd") or {}).items()
            },
            vault_dir=Path(env.get("WORKSPACE_VAULT_DIR") or WORKSPACE_VAULT_DIR),
            meta_ads_currency=str(
                ((config.get("providers") or {}).get("campaigns") or {}).get(
                    "meta_ads_currency", "COP"
                )
            ),
            whatsapp_token=_secret(env, "WHATSAPP_ACCESS_TOKEN"),
            whatsapp_waba_id=_secret(env, "WHATSAPP_BUSINESS_ACCOUNT_ID"),
            temporal_api_key=_secret(env, "TEMPORAL_API_KEY"),
            temporal_namespace=_secret(env, "TEMPORAL_NAMESPACE"),
            temporal_prices=TemporalPrices(
                usd_per_million_actions=Decimal(str(temporal.get("usd_per_million_actions", "50"))),
                usd_per_active_gbh=Decimal(str(temporal.get("usd_per_active_gbh", "0.042"))),
                usd_per_retained_gbh=Decimal(str(temporal.get("usd_per_retained_gbh", "0.00105"))),
                plan_fee_ratio=Decimal(str(temporal.get("plan_fee_ratio", "0.10"))),
            ),
        )


def build_providers(s: CostsSettings) -> list[CostProvider]:
    """The cards of the tab, in the order the operator reads them."""
    campaigns = AdsCampaignsReader()
    return [
        *conversation_providers(ConversationCostsReader(s.vault_dir)),
        WhatsAppProvider(
            token=s.whatsapp_token, waba_id=s.whatsapp_waba_id, campaigns=campaigns
        ),
        CampaignsProvider(campaigns, meta_currency=s.meta_ads_currency),
        TemporalProvider(
            api_key=s.temporal_api_key,
            namespace=s.temporal_namespace,
            prices=s.temporal_prices,
        ),
    ]
