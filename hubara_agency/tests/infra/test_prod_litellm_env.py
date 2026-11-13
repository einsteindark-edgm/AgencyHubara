"""LiteLLM de producción con su propio archivo de entorno (memoria de la caja
de app, 2026-11-05).

Hasta hoy el proxy leía el `.env` entero de la caja: todas las llaves de
producción (Meta, WhatsApp, Medusa, Temporal, Cognito…) y `HUBARA_IMAGE`, que
cambia en cada deploy. Compose recrea un contenedor cuando cambia su entorno, y
además el deploy lo borraba a propósito (`rm -sf litellm`): cada deploy
reiniciaba LiteLLM, que arranca en ~990 MB y con los días baja a ~200 (lo que no
usa se va a swap). Las alarmas de poca memoria del 8, 13, 14 y 21 de octubre y
del 5 de noviembre sonaron justo después de un deploy.

Ahora `render-env-from-ssm.sh` le arma `litellm.env` con solo las llaves que
nombra su config (`os.environ/…`) y la huella del config: compose lo recrea
cuando cambia una de esas cosas (o su imagen), no con cada deploy.

Se corre el script con un `aws` falso en el PATH y la caja en un directorio
temporal (`HUBARA_DIR`).
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[3]
_RENDER = _REPO / "infra" / "compose" / "render-env-from-ssm.sh"
_COMPOSE = _REPO / "infra" / "compose" / "docker-compose.prod.yml"
_DEPLOY = _REPO / ".github" / "workflows" / "backend-deploy.yml"
_REAL_CONFIG = _REPO / "exoclaw-temporal" / "litellm_config.yaml"

_CONFIG = """model_list:
  - model_name: deepseek-v4-flash
    litellm_params:
      model: deepseek/deepseek-v4-flash
      api_key: "os.environ/DEEPSEEK_API_KEY"
  - model_name: gemini-backup
    litellm_params:
      model: gemini/gemini-3.5-flash-lite
      api_key: "os.environ/GEMINI_API_KEY"
  - model_name: gemini-photo-match
    litellm_params:
      model: gemini/gemini-3.5-flash-lite
      api_key: "os.environ/GEMINI_API_KEY"
"""

_PARAMS = {
    "DEEPSEEK_API_KEY": "sk-deepseek-1",
    "GEMINI_API_KEY": "sk-gemini-1",
    "WHATSAPP_APP_SECRET": "wa-secret",
    "COGNITO_USER_POOL_ID": "us-east-1_pool",
    "COGNITO_APP_CLIENT_ID": "client",
    "HUBARA_SERVICE_TOKEN": "svc-token",
    "META_SYSTEM_USER_TOKEN": "meta-token",
}


def _box(tmp_path: Path, *, config: str = _CONFIG) -> Path:
    box = tmp_path / "opt-hubara"
    box.mkdir(exist_ok=True)
    (box / "box.env").write_text(
        "AWS_REGION=us-east-1\nTENANT=hubara\nCADDY_DOMAIN=example.test\nENABLED_PLUGINS=\nIMAGE_REPO=x\n",
        encoding="utf-8",
    )
    (box / "litellm_config.yaml").write_text(config, encoding="utf-8")
    return box


def _render(tmp_path: Path, box: Path, *, image: str = "ghcr.io/x/agencyhubara:aaa", params: dict | None = None) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    lines = "".join(f"/hubara/hubara/{k}\\t{v}\\n" for k, v in (params or _PARAMS).items())
    fake = bin_dir / "aws"
    fake.write_text(f"#!/bin/bash\nprintf '{lines}'\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "HUBARA_DIR": str(box),
           "HUBARA_IMAGE": image, "OTEL_ENDPOINT": ""}
    subprocess.run(["bash", str(_RENDER)], check=True, env=env, capture_output=True, text=True)


def _env_file(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line)


def test_litellm_gets_only_the_keys_its_config_names(tmp_path: Path) -> None:
    box = _box(tmp_path)
    _render(tmp_path, box)

    litellm = _env_file(box / "litellm.env")

    assert {k: v for k, v in litellm.items() if k != "LITELLM_CONFIG_SHA256"} == {
        "DEEPSEEK_API_KEY": "sk-deepseek-1",
        "GEMINI_API_KEY": "sk-gemini-1",
    }
    assert re.fullmatch(r"[0-9a-f]{64}", litellm["LITELLM_CONFIG_SHA256"])
    assert "WHATSAPP_APP_SECRET" in _env_file(box / ".env")  # el resto del stack sigue igual
    assert stat.S_IMODE((box / "litellm.env").stat().st_mode) == 0o600


def test_a_new_image_does_not_touch_what_litellm_sees(tmp_path: Path) -> None:
    box = _box(tmp_path)
    _render(tmp_path, box, image="ghcr.io/x/agencyhubara:aaa")
    before = (box / "litellm.env").read_bytes()

    _render(tmp_path, box, image="ghcr.io/x/agencyhubara:bbb")

    assert _env_file(box / ".env")["HUBARA_IMAGE"] == "ghcr.io/x/agencyhubara:bbb"
    assert (box / "litellm.env").read_bytes() == before


def test_a_change_in_its_config_or_its_keys_does_change_it(tmp_path: Path) -> None:
    box = _box(tmp_path)
    _render(tmp_path, box)
    first = (box / "litellm.env").read_bytes()

    (box / "litellm_config.yaml").write_text(_CONFIG + "router_settings:\n  num_retries: 1\n", encoding="utf-8")
    _render(tmp_path, box)
    after_config = (box / "litellm.env").read_bytes()

    _render(tmp_path, box, params={**_PARAMS, "DEEPSEEK_API_KEY": "sk-deepseek-2"})
    after_key = (box / "litellm.env").read_bytes()

    assert len({first, after_config, after_key}) == 3
    assert _env_file(box / "litellm.env")["DEEPSEEK_API_KEY"] == "sk-deepseek-2"


def test_every_key_the_real_config_names_reaches_litellm(tmp_path: Path) -> None:
    named = set(re.findall(r"os\.environ/([A-Z0-9_]+)", _REAL_CONFIG.read_text(encoding="utf-8")))
    box = _box(tmp_path, config=_REAL_CONFIG.read_text(encoding="utf-8"))
    _render(tmp_path, box)

    assert named and named <= set(_env_file(box / "litellm.env"))


def test_prod_compose_gives_litellm_only_its_own_env_file() -> None:
    services = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))["services"]
    env_file = services["litellm"]["env_file"]

    assert (env_file if isinstance(env_file, list) else [env_file]) == ["litellm.env"]
    assert "environment" not in services["litellm"]


def test_the_deploy_does_not_force_litellm_to_restart() -> None:
    script = _DEPLOY.read_text(encoding="utf-8")

    assert not re.search(r"compose\s+rm\b[^\n]*\blitellm\b", script)
    assert "--force-recreate" not in script
    assert "exoclaw-temporal/litellm_config.yaml" in script  # el config sigue llegando a la caja
