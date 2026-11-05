"""Settings of the Costs tab: secrets from the environment, knobs from costs.yaml.

A secret that is missing, empty or still the SSM placeholder means the
provider is *not connected* — its adapter never calls anyone and the card says
how to connect it.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from src.plugins.costs.providers.base import CostProvider
from src.plugins.costs.providers.deepseek import DeepSeekProvider
from src.plugins.costs.providers.gemini import GeminiProvider
from src.plugins.costs.providers.openrouter import OpenRouterProvider
from src.plugins.costs.providers.temporal import TemporalPrices, TemporalProvider
from src.plugins.costs.providers.whatsapp import WhatsAppProvider
from src.sdk.runtime import is_placeholder

CONFIG_PATH = Path(__file__).parent / "config" / "costs.yaml"


def load_config(path: Path = CONFIG_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _secret(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    return None if is_placeholder(value) else str(value).strip()


def _keys(env: Mapping[str, str], name: str, default: list) -> tuple[str, ...]:
    raw = env.get(name)
    if raw and not is_placeholder(raw):
        return tuple(k.strip() for k in raw.split(",") if k.strip())
    return tuple(str(k) for k in default or [])


def _service_account(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


@dataclass(frozen=True)
class CostsSettings:
    timeout_s: float
    live_ttl_s: float
    closed_ttl_s: float
    fx_fallback_cop_per_usd: Decimal
    fx_per_usd: dict[str, Decimal]
    deepseek_platform_token: str | None
    deepseek_api_key: str | None
    deepseek_production_keys: tuple[str, ...]
    openrouter_management_key: str | None
    openrouter_api_key: str | None
    openrouter_production_keys: tuple[str, ...]
    gcp_service_account: dict | None
    gemini_dataset: str
    gemini_project: str | None
    whatsapp_token: str | None
    whatsapp_waba_id: str | None
    temporal_api_key: str | None
    temporal_namespace: str | None
    temporal_prices: TemporalPrices

    @classmethod
    def from_env(cls, env: Mapping[str, str], config: dict) -> CostsSettings:
        cache = config.get("cache") or {}
        fx = config.get("fx") or {}
        prov = config.get("providers") or {}
        deepseek = prov.get("deepseek") or {}
        openrouter = prov.get("openrouter") or {}
        gemini = prov.get("gemini") or {}
        temporal = prov.get("temporal") or {}
        return cls(
            timeout_s=float(config.get("timeout_seconds", 25)),
            live_ttl_s=float(cache.get("live_ttl_seconds", 600)),
            closed_ttl_s=float(cache.get("closed_ttl_seconds", 21600)),
            fx_fallback_cop_per_usd=Decimal(str(fx.get("cop_per_usd_fallback", "4000"))),
            fx_per_usd={
                str(cur).upper(): Decimal(str(rate))
                for cur, rate in (fx.get("per_usd") or {}).items()
            },
            deepseek_platform_token=_secret(env, "DEEPSEEK_PLATFORM_TOKEN"),
            deepseek_api_key=_secret(env, "DEEPSEEK_API_KEY"),
            deepseek_production_keys=_keys(
                env, "COSTS_DEEPSEEK_PRODUCTION_KEYS", deepseek.get("production_keys")
            ),
            openrouter_management_key=_secret(env, "OPENROUTER_MANAGEMENT_KEY"),
            openrouter_api_key=_secret(env, "OPENROUTER_API_KEY"),
            openrouter_production_keys=_keys(
                env, "COSTS_OPENROUTER_PRODUCTION_KEYS", openrouter.get("production_keys")
            ),
            gcp_service_account=_service_account(
                _secret(env, "GCP_BILLING_SERVICE_ACCOUNT_JSON")
            ),
            gemini_dataset=str(gemini.get("dataset") or "billing_export"),
            gemini_project=gemini.get("project") or None,
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
    return [
        DeepSeekProvider(
            platform_token=s.deepseek_platform_token,
            api_key=s.deepseek_api_key,
            production_keys=s.deepseek_production_keys,
        ),
        OpenRouterProvider(
            management_key=s.openrouter_management_key,
            api_key=s.openrouter_api_key,
            production_keys=s.openrouter_production_keys,
        ),
        GeminiProvider(
            service_account=s.gcp_service_account,
            dataset=s.gemini_dataset,
            project=s.gemini_project,
        ),
        WhatsAppProvider(token=s.whatsapp_token, waba_id=s.whatsapp_waba_id),
        TemporalProvider(
            api_key=s.temporal_api_key,
            namespace=s.temporal_namespace,
            prices=s.temporal_prices,
        ),
    ]
