"""Which providers are connected comes from the environment; the knobs from costs.yaml."""
from __future__ import annotations

import json
from decimal import Decimal

from src.plugins.costs.settings import CostsSettings, build_providers, load_config

PLACEHOLDER = "PLACEHOLDER_set_out_of_band"  # the value Terraform writes in SSM


def _by_id(settings: CostsSettings) -> dict:
    return {p.id: p for p in build_providers(settings)}


def test_the_tab_lists_the_five_providers_in_a_fixed_order() -> None:
    settings = CostsSettings.from_env({}, load_config())

    assert [p.id for p in build_providers(settings)] == [
        "deepseek", "openrouter", "gemini", "whatsapp", "temporal",
    ]


def test_ssm_placeholders_mean_not_connected() -> None:
    env = {
        "DEEPSEEK_PLATFORM_TOKEN": PLACEHOLDER,
        "OPENROUTER_MANAGEMENT_KEY": "",
        "GCP_BILLING_SERVICE_ACCOUNT_JSON": PLACEHOLDER,
        "WHATSAPP_ACCESS_TOKEN": PLACEHOLDER,
        "WHATSAPP_BUSINESS_ACCOUNT_ID": "123",
        "TEMPORAL_API_KEY": PLACEHOLDER,
    }
    providers = _by_id(CostsSettings.from_env(env, load_config()))

    assert not any(p.connected for p in providers.values())


def test_real_values_connect_each_provider() -> None:
    env = {
        "DEEPSEEK_PLATFORM_TOKEN": "session",
        "OPENROUTER_MANAGEMENT_KEY": "sk-or-mgmt",
        "GCP_BILLING_SERVICE_ACCOUNT_JSON": json.dumps({"project_id": "p", "client_email": "e"}),
        "WHATSAPP_ACCESS_TOKEN": "t",
        "WHATSAPP_BUSINESS_ACCOUNT_ID": "123",
        "TEMPORAL_API_KEY": "k",
        "TEMPORAL_NAMESPACE": "hubara.ri1ti",
    }
    providers = _by_id(CostsSettings.from_env(env, load_config()))

    assert all(p.connected for p in providers.values())


def test_broken_service_account_json_is_not_connected() -> None:
    settings = CostsSettings.from_env({"GCP_BILLING_SERVICE_ACCOUNT_JSON": "{not json"}, load_config())

    assert _by_id(settings)["gemini"].connected is False


def test_yaml_holds_the_production_keys_and_temporal_prices() -> None:
    settings = CostsSettings.from_env({}, load_config())

    assert "Hubara Ventas" in settings.deepseek_production_keys
    assert settings.temporal_prices.usd_per_million_actions == Decimal("50")
    assert settings.temporal_prices.plan_fee_ratio == Decimal("0.10")
    assert settings.fx_per_usd["CNY"] > 0


def test_env_can_override_the_production_keys_per_tenant() -> None:
    env = {"COSTS_DEEPSEEK_PRODUCTION_KEYS": "Tienda Ventas, Tienda Jev"}
    settings = CostsSettings.from_env(env, load_config())

    assert settings.deepseek_production_keys == ("Tienda Ventas", "Tienda Jev")
