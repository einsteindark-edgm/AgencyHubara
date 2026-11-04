"""Las alertas del scorecard apuntan al repo privado del ciclo (Ciclo Colossus F4).

Regla del operador: todo parámetro que el código lee nace en Terraform, por
tenant. El repo (`SCORECARD_ALERTS_REPO`) es config, no secreto: va en
`modules/eval-alerts-config` desde `tenants.<t>.eval_alerts.repo` (git =
verdad). El token (`SCORECARD_ALERTS_GITHUB_TOKEN`) es secreto: la CLAVE en
`secret_keys` con placeholder y el VALOR fuera de banda (un token fino con
Issues de lectura y escritura solo en ese repo). Hasta que el operador lo pone,
el placeholder no cuenta como configuración y no se llama a GitHub.
"""
from __future__ import annotations

import re
from pathlib import Path

from src.plugins.chats.agent.sales_eval.scorecard.alerts import REPO_ENV, TOKEN_ENV

_TERRAFORM = Path(__file__).resolve().parents[3] / "infra" / "terraform" / "platform"
CYCLE_REPO = "einsteindark-edgm/hubara-ciclo"


def _hcl(path: Path) -> str:
    return "\n".join(line.split("#", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())


def test_the_token_is_a_secret_key_set_out_of_band() -> None:
    assert f'"{TOKEN_ENV}"' in _hcl(_TERRAFORM / "variables.tf"), f"{TOKEN_ENV} falta en secret_keys"


def test_the_repo_is_a_tenant_parameter_born_off() -> None:
    module = _hcl(_TERRAFORM / "modules" / "eval-alerts-config" / "main.tf")
    assert re.search(rf"^\s*{REPO_ENV}\s*=", module, re.M), f"eval-alerts-config no declara {REPO_ENV}"

    variables = _hcl(_TERRAFORM / "variables.tf")
    assert re.search(r'repo\s*=\s*optional\(string,\s*""\)', variables), "un tenant nuevo nace sin alertas"
    assert "t.eval_alerts.repo" in variables, "falta validar que el repo sea owner/repo"
    assert 'source   = "./modules/eval-alerts-config"' in _hcl(_TERRAFORM / "main.tf")


def test_hubara_sends_its_alerts_to_the_private_cycle_repo() -> None:
    tfvars = _hcl(_TERRAFORM / "tenants.auto.tfvars")
    hubara = tfvars[tfvars.index("hubara = {"):tfvars.index("vincenzo = {")]

    assert re.search(rf'eval_alerts\s*=\s*\{{\s*repo\s*=\s*"{re.escape(CYCLE_REPO)}"\s*\}}', hubara)
