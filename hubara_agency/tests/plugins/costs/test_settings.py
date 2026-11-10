"""Which cards the tab has, where each reads from, and the knobs of costs.yaml."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from src.plugins.costs.settings import CostsSettings, build_providers, load_config

PLACEHOLDER = "PLACEHOLDER_set_out_of_band"  # the value Terraform writes in SSM


def _by_id(settings: CostsSettings) -> dict:
    return {p.id: p for p in build_providers(settings)}


def test_the_tab_lists_the_cards_in_a_fixed_order() -> None:
    settings = CostsSettings.from_env({}, load_config())

    assert [p.id for p in build_providers(settings)] == [
        "deepseek", "openrouter", "gemini", "whatsapp", "campaigns", "temporal",
    ]


def test_llm_cards_need_no_credential_they_read_the_conversations() -> None:
    providers = _by_id(CostsSettings.from_env({}, load_config()))

    assert all(providers[p].connected for p in ("deepseek", "openrouter", "gemini"))


def test_ssm_placeholders_leave_whatsapp_and_temporal_not_connected() -> None:
    env = {
        "WHATSAPP_ACCESS_TOKEN": PLACEHOLDER,
        "WHATSAPP_BUSINESS_ACCOUNT_ID": "123",
        "TEMPORAL_API_KEY": PLACEHOLDER,
    }
    providers = _by_id(CostsSettings.from_env(env, load_config()))

    assert providers["whatsapp"].connected is False
    assert providers["temporal"].connected is False


def test_real_values_connect_whatsapp_and_temporal() -> None:
    env = {
        "WHATSAPP_ACCESS_TOKEN": "t",
        "WHATSAPP_BUSINESS_ACCOUNT_ID": "123",
        "TEMPORAL_API_KEY": "k",
        "TEMPORAL_NAMESPACE": "hubara.ri1ti",
    }
    providers = _by_id(CostsSettings.from_env(env, load_config()))

    assert providers["whatsapp"].connected and providers["temporal"].connected


def test_conversations_are_read_from_the_vault_of_this_environment() -> None:
    settings = CostsSettings.from_env({"WORKSPACE_VAULT_DIR": "/mnt/vault"}, load_config())

    assert settings.vault_dir == Path("/mnt/vault")


def test_yaml_holds_temporal_prices_and_exchange_rates() -> None:
    settings = CostsSettings.from_env({}, load_config())

    assert settings.temporal_prices.usd_per_million_actions == Decimal("50")
    assert settings.temporal_prices.plan_fee_ratio == Decimal("0.10")
    assert settings.fx_per_usd["CNY"] > 0
