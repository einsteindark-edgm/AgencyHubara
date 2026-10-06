"""Guard de arranque del worker `sales_lab` (plan §3.5, candado 3).

El worker del laboratorio corre la MISMA imagen que producción. No arranca si:
  * encuentra una llave de producción en su entorno (WhatsApp, Medusa, Meta,
    Temporal Cloud, Cognito, token de servicio, datos bancarios);
  * `TEMPORAL_URL` no es el Temporal de la caja (`temporal:7233`) o el
    namespace no es `hubara-lab`;
  * alguna carpeta (vault, historial del LLM, catálogo, evals) apunta fuera
    de `/lab`.
Las carpetas se FIJAN antes de importar la app (la composición guarda rutas y
clientes con `lru_cache`).
"""
from __future__ import annotations

import pytest

from src.plugins.chats.agent.sales_lab.guard import check_lab_env, prepare_lab_env

GOOD = {
    "TEMPORAL_URL": "temporal:7233",
    "TEMPORAL_NAMESPACE": "hubara-lab",
    "HUBARA_ENV": "lab",
    "LAB_ROOT": "/lab",
    "DEEPSEEK_API_KEY": "sk-deepseek",
    "OPENROUTER_API_KEY": "sk-or-lab",
}


def _prepared(**over) -> dict:
    env = {**GOOD, **over}
    prepare_lab_env(env)
    return env


def test_prepare_pins_every_folder_inside_the_lab_root() -> None:
    env = _prepared()

    for var in ("WORKSPACE_VAULT_DIR", "EXOCLAW_STATE_DIR", "CATALOG_SNAPSHOT_DIR", "EVAL_CANDIDATES_DIR"):
        assert env[var].startswith("/lab/"), var
    assert env["OTEL_SDK_DISABLED"] == "true"
    assert check_lab_env(env) == []


@pytest.mark.parametrize(
    "key",
    [
        "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "MEDUSA_ADMIN_TOKEN", "MEDUSA_BASE_URL",
        "META_SYSTEM_USER_TOKEN", "TEMPORAL_API_KEY", "TEMPORAL_ADDRESS", "HUBARA_SERVICE_TOKEN",
        "COGNITO_USER_POOL_ID", "PAYMENT_TRANSFER_ACCOUNT_NUMBER",
    ],
)
def test_a_production_key_blocks_the_start(key: str) -> None:
    problems = check_lab_env(_prepared(**{key: "real-value"}))

    assert any(key in p for p in problems)


def test_the_sandbox_phone_id_sentinel_is_the_only_whatsapp_value_allowed() -> None:
    """El envío simulado exige `WHATSAPP_PHONE_NUMBER_ID` aunque no haya
    llave: el sandbox pone un valor centinela. Solo ESE valor pasa; la llave
    (`WHATSAPP_ACCESS_TOKEN`) sigue prohibida y es la que impide enviar."""
    assert check_lab_env(_prepared(WHATSAPP_PHONE_NUMBER_ID="lab-sandbox")) == []
    assert check_lab_env(_prepared(WHATSAPP_PHONE_NUMBER_ID="1234567890")) != []
    assert check_lab_env(_prepared(WHATSAPP_PHONE_NUMBER_ID="lab-sandbox", WHATSAPP_ACCESS_TOKEN="EAAG")) != []


def test_an_empty_production_variable_is_not_a_key() -> None:
    assert check_lab_env(_prepared(MEDUSA_BASE_URL="")) == []


@pytest.mark.parametrize(
    ("over", "needle"),
    [
        ({"TEMPORAL_URL": "us-west-2.aws.api.temporal.io:7233"}, "TEMPORAL_URL"),
        ({"TEMPORAL_NAMESPACE": "hubara.ri1ti"}, "TEMPORAL_NAMESPACE"),
        ({"HUBARA_ENV": "production"}, "HUBARA_ENV"),
        ({"WORKSPACE_VAULT_DIR": "/app/hubara_vault"}, "WORKSPACE_VAULT_DIR"),
        ({"EXOCLAW_STATE_DIR": "/lab/../app/hubara_vault/agent_state"}, "EXOCLAW_STATE_DIR"),
    ],
)
def test_wrong_temporal_or_a_folder_outside_the_lab_blocks_the_start(over: dict, needle: str) -> None:
    env = {**GOOD}
    prepare_lab_env(env)
    env.update(over)

    assert any(needle in p for p in check_lab_env(env))


def test_prepare_never_overrides_a_folder_already_inside_the_lab() -> None:
    env = _prepared(WORKSPACE_VAULT_DIR="/lab/runs/r1/A1/0/t1/vault")

    assert env["WORKSPACE_VAULT_DIR"] == "/lab/runs/r1/A1/0/t1/vault"
