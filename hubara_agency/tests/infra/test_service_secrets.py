"""Cada contenedor de prod recibe solo los secretos que usa.

Antes, `render-env-from-ssm.sh` escribía TODO `/hubara/<tenant>/*` en un solo
`.env` y los 14 contenedores lo cargaban: el de LiteLLM (imagen de terceros, la
misma semana en que publicaron una LiteLLM con malware) tenía el token de
WhatsApp, el admin de Medusa, la llave de Temporal y la cuenta de transferencias.
Revisión de seguridad del 2026-10-26.

Ahora:
- `.env` lleva solo la config que no es secreta (parámetros `String` + el bloque fijo).
- `secrets/<servicio>.env` lleva los `SecureString` que ese servicio usa, según
  `infra/compose/service-secrets.json`.
- `.env.host` lleva lo que solo usa la caja (el token de GHCR del deploy).
- `litellm.env` lleva solo las llaves que nombra la config de LiteLLM y la huella
  de esa config (guarda propia: `test_prod_litellm_env.py`).

El mapa lo genera `scripts/service_secrets.py` con el grafo de imports de cada
servicio, así que una guarda lo compara con el código: si un servicio empieza a
leer un secreto nuevo, el PR tiene que regenerar el mapa.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[3]
_COMPOSE = _REPO / "infra" / "compose" / "docker-compose.prod.yml"
_MAP = _REPO / "infra" / "compose" / "service-secrets.json"
_SPLIT = _REPO / "infra" / "compose" / "split-env-by-service.py"
_GENERATOR = _REPO / "hubara_agency" / "scripts" / "service_secrets.py"
_DEPLOY = _REPO / ".github" / "workflows" / "backend-deploy.yml"
_VARIABLES = _REPO / "infra" / "terraform" / "platform" / "variables.tf"
_REGENERATE = "cd hubara_agency && uv run python scripts/service_secrets.py --write"


def _services() -> dict[str, dict]:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))["services"]


def _app_services() -> list[str]:
    return sorted(n for n, s in _services().items() if s.get("image") == "${HUBARA_IMAGE}")


def _map() -> dict[str, list[str]]:
    assert _MAP.exists(), f"falta el mapa de secretos por servicio: {_REGENERATE}"
    return json.loads(_MAP.read_text(encoding="utf-8"))


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"no se puede cargar {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ── El mapa ──────────────────────────────────────────────────────────────────


def test_the_map_matches_what_the_code_of_each_service_reads() -> None:
    generator = _load(_GENERATOR, "service_secrets")

    computed = generator.compute_map()

    assert _map() == computed, f"el mapa quedó viejo frente al código; regeneralo: {_REGENERATE}"


def test_every_app_container_of_the_compose_is_in_the_map() -> None:
    services = {s for s in _map() if not s.startswith("_")}
    assert services == set(_app_services()), f"servicios del mapa != compose: {sorted(services ^ set(_app_services()))}"


def test_the_map_notes_the_model_keys_as_litellm_s() -> None:
    # LiteLLM no es un servicio del mapa: su `litellm.env` sale de su config. La
    # entrada `_litellm` hace que esas llaves tengan dueño (no van a `_none`).
    assert _map()["_litellm"] == ["DEEPSEEK_API_KEY", "GEMINI_API_KEY"]


def test_the_registry_token_never_reaches_a_container() -> None:
    m = _map()
    assert m["_host"] == ["GHCR_PULL_TOKEN"]
    assert not [s for s, keys in m.items() if not s.startswith("_") and "GHCR_PULL_TOKEN" in keys]


def test_every_secret_terraform_declares_has_an_owner() -> None:
    text = _VARIABLES.read_text(encoding="utf-8")
    declared = set(re.findall(r'"([A-Z][A-Z0-9_]+)"', text[text.index("secret_keys") :].split("]", 1)[0]))
    owned = {k for keys in _map().values() for k in keys}
    assert declared <= owned, f"secretos de Terraform sin dueño en el mapa (ni _none): {sorted(declared - owned)}"


# ── El compose ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", _app_services())
def test_each_app_container_loads_the_shared_config_and_only_its_secrets(name: str) -> None:
    assert _services()[name].get("env_file") == [".env", f"secrets/{name}.env"]


def test_caddy_gets_no_environment_file() -> None:
    assert "env_file" not in _services()["caddy"]


# ── El reparto (split-env-by-service.py, corre en la caja con python3.9) ────


def _params() -> list[dict]:
    p = [
        {"Name": "/hubara/hubara/COGNITO_USER_POOL_ID", "Type": "String", "Value": "us-east-1_pool"},
        {"Name": "/hubara/hubara/COGNITO_APP_CLIENT_ID", "Type": "String", "Value": "client"},
        {"Name": "/hubara/hubara/scheduler/WATCHDOG_QUIET_HOURS_START", "Type": "String", "Value": "21:30"},
        {"Name": "/hubara/hubara/WHATSAPP_APP_SECRET", "Type": "SecureString", "Value": "app-secret"},
        {"Name": "/hubara/hubara/HUBARA_SERVICE_TOKEN", "Type": "SecureString", "Value": "svc-token"},
        {"Name": "/hubara/hubara/DEEPSEEK_API_KEY", "Type": "SecureString", "Value": "sk-deepseek"},
        {"Name": "/hubara/hubara/GEMINI_API_KEY", "Type": "SecureString", "Value": "sk-gemini"},
        {"Name": "/hubara/hubara/GHCR_PULL_TOKEN", "Type": "SecureString", "Value": "ghp_x"},
        {"Name": "/hubara/hubara/meta/oauth", "Type": "SecureString", "Value": '{"token": "t"}'},
        {"Name": "/hubara/hubara/NUEVO_SECRETO", "Type": "SecureString", "Value": "nuevo"},
    ]
    return p


_FAKE_MAP = {
    "api": ["DEEPSEEK_API_KEY", "HUBARA_SERVICE_TOKEN", "WHATSAPP_APP_SECRET"],
    "worker-chats-sales": ["DEEPSEEK_API_KEY"],
    "_litellm": ["DEEPSEEK_API_KEY", "GEMINI_API_KEY"],
    "_host": ["GHCR_PULL_TOKEN"],
    "_none": ["oauth"],
}
_FAKE_LITELLM_CONFIG = (
    'model_list:\n'
    '  - litellm_params: {model: deepseek/x, api_key: "os.environ/DEEPSEEK_API_KEY"}\n'
    '  - litellm_params: {model: gemini/x, api_key: "os.environ/GEMINI_API_KEY"}\n'
)


def _split(tmp_path: Path, params: list[dict]) -> subprocess.CompletedProcess:
    (tmp_path / "params.json").write_text(json.dumps({"Parameters": params}))
    (tmp_path / "map.json").write_text(json.dumps(_FAKE_MAP))
    (tmp_path / "static.env").write_text("HUBARA_ENV=production\nCOGNITO_APP_CLIENT_ID=gana-el-fijo\n")
    (tmp_path / "litellm_config.yaml").write_text(_FAKE_LITELLM_CONFIG)
    return subprocess.run(
        [sys.executable, str(_SPLIT), "--params", str(tmp_path / "params.json"), "--map", str(tmp_path / "map.json"),
         "--static", str(tmp_path / "static.env"), "--litellm-config", str(tmp_path / "litellm_config.yaml"),
         "--out-dir", str(tmp_path / "out")],
        capture_output=True, text=True,
    )


def _lines(path: Path) -> list[str]:
    return path.read_text().splitlines()


def test_the_shared_env_keeps_only_the_non_secret_config(tmp_path: Path) -> None:
    result = _split(tmp_path, _params())

    assert result.returncode == 0, result.stderr
    shared = _lines(tmp_path / "out" / ".env")
    assert "COGNITO_USER_POOL_ID=us-east-1_pool" in shared and "WATCHDOG_QUIET_HOURS_START=21:30" in shared
    assert shared[-1] == "COGNITO_APP_CLIENT_ID=gana-el-fijo", "el bloque fijo va al final: el último gana"
    assert not [line for line in shared if line.split("=", 1)[0] in {"WHATSAPP_APP_SECRET", "DEEPSEEK_API_KEY", "GHCR_PULL_TOKEN", "oauth"}]


def test_each_service_gets_only_its_secrets_and_the_host_keeps_the_registry_token(tmp_path: Path) -> None:
    _split(tmp_path, _params())
    out = tmp_path / "out"

    assert [line for line in _lines(out / "litellm.env") if not line.startswith("LITELLM_CONFIG_SHA256=")] == [
        "DEEPSEEK_API_KEY=sk-deepseek", "GEMINI_API_KEY=sk-gemini"]
    assert not (out / "secrets" / "litellm.env").exists(), "LiteLLM no tiene archivo en secrets/: usa litellm.env"
    api = _lines(out / "secrets" / "api.env")
    assert "WHATSAPP_APP_SECRET=app-secret" in api and "HUBARA_SERVICE_TOKEN=svc-token" in api
    assert not [line for line in api if line.startswith(("GHCR_PULL_TOKEN=", "oauth="))]
    assert _lines(out / ".env.host") == ["GHCR_PULL_TOKEN=ghp_x"]
    assert stat_mode(out / "secrets" / "api.env") == 0o600 and stat_mode(out / ".env.host") == 0o600
    assert stat_mode(out / "litellm.env") == 0o600


def test_a_key_only_litellm_uses_reaches_no_app_container(tmp_path: Path) -> None:
    # GEMINI_API_KEY solo la nombra la config de LiteLLM: ningún servicio de la app
    # la recibe, y no es un secreto sin dueño (no avisa).
    result = _split(tmp_path, _params())
    out = tmp_path / "out"

    assert result.returncode == 0, result.stderr
    assert "GEMINI_API_KEY=sk-gemini" in _lines(out / "litellm.env")
    for f in (out / "secrets").glob("*.env"):
        assert "GEMINI_API_KEY" not in f.read_text(), f.name
    assert "GEMINI_API_KEY" not in result.stderr


def test_a_secret_without_owner_goes_to_the_app_services_but_never_to_litellm(tmp_path: Path) -> None:
    result = _split(tmp_path, _params())
    out = tmp_path / "out" / "secrets"

    assert "NUEVO_SECRETO=nuevo" in _lines(out / "api.env") and "NUEVO_SECRETO=nuevo" in _lines(out / "worker-chats-sales.env")
    assert "NUEVO_SECRETO" not in (tmp_path / "out" / "litellm.env").read_text()
    assert "NUEVO_SECRETO" in result.stderr, "un secreto sin dueño tiene que avisar en el log del deploy"


def test_a_repeated_name_keeps_the_value_it_has_today(tmp_path: Path) -> None:
    # Prod, 2026-10-26: `/WATCHDOG_ENABLED` (SecureString, true) y
    # `/scheduler/WATCHDOG_ENABLED` (String, false). En el `.env` de un solo
    # archivo gana la ÚLTIMA línea (el orden de SSM): false. Si el SecureString
    # fuera al archivo de secretos, que se carga después, el watchdog se prendería.
    params = _params() + [
        {"Name": "/hubara/hubara/WATCHDOG_ENABLED", "Type": "SecureString", "Value": "true"},
        {"Name": "/hubara/hubara/scheduler/WATCHDOG_ENABLED", "Type": "String", "Value": "false"},
        {"Name": "/hubara/hubara/scheduler/HUBARA_SERVICE_TOKEN_TTL", "Type": "String", "Value": "60"},
    ]

    _split(tmp_path, params)
    out = tmp_path / "out"

    assert "WATCHDOG_ENABLED=false" in _lines(out / ".env")
    for f in (out / "secrets").glob("*.env"):
        assert "WATCHDOG_ENABLED" not in f.read_text(), f.name


def test_when_the_secret_comes_last_it_wins_and_stays_out_of_the_shared_env(tmp_path: Path) -> None:
    params = [{"Name": "/hubara/hubara/scheduler/DEEPSEEK_API_KEY", "Type": "String", "Value": "viejo"}] + _params()

    _split(tmp_path, params)
    out = tmp_path / "out"

    assert "DEEPSEEK_API_KEY" not in (out / ".env").read_text()
    assert "DEEPSEEK_API_KEY=sk-deepseek" in _lines(out / "litellm.env")


@pytest.mark.parametrize("missing", ["COGNITO_USER_POOL_ID", "WHATSAPP_APP_SECRET", "HUBARA_SERVICE_TOKEN"])
def test_the_deploy_still_aborts_without_the_auth_parameters(tmp_path: Path, missing: str) -> None:
    params = [p for p in _params() if not p["Name"].endswith("/" + missing)]

    result = _split(tmp_path, params)

    assert result.returncode != 0 and missing in result.stderr
    assert not (tmp_path / "out" / ".env").exists(), "sin auth no se escribe nada: el contenedor viejo sigue sirviendo"
    assert not (tmp_path / "out" / "litellm.env").exists()


def test_a_terraform_placeholder_counts_as_missing(tmp_path: Path) -> None:
    params = [dict(p, Value="PLACEHOLDER_set_out_of_band") if p["Name"].endswith("/HUBARA_SERVICE_TOKEN") else p for p in _params()]

    result = _split(tmp_path, params)

    assert result.returncode != 0 and "HUBARA_SERVICE_TOKEN" in result.stderr


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777


# ── El deploy ────────────────────────────────────────────────────────────────


def test_the_deploy_ships_the_split_and_reads_the_registry_token_from_the_host_file() -> None:
    text = _DEPLOY.read_text(encoding="utf-8")
    for path in ("infra/compose/service-secrets.json", "infra/compose/split-env-by-service.py",
                 "infra/compose/refresh-container-aws-creds.sh", "infra/compose/app-aws-creds.service",
                 "infra/compose/app-aws-creds.timer"):
        assert path in text, f"el deploy no copia {path} a la caja"
    assert "grep '^GHCR_PULL_TOKEN=' .env.host" in text
    assert "grep '^GHCR_PULL_TOKEN=' .env |" not in text and "grep '^GHCR_PULL_TOKEN=' .env)" not in text
    assert "systemctl enable --now app-aws-creds.timer" in text
