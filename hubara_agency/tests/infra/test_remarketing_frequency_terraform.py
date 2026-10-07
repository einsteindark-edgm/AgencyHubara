"""El techo de la frecuencia del remarketing nace en Terraform.

El dashboard (Agents → Remarketing → Frecuencia) elige cuántos toques hace el
bot DENTRO de un techo; el techo lo lee la API y los workers del `.env` que
`render-env-from-ssm.sh` arma desde SSM `/hubara/<tenant>/`. Regla del
operador: todo parámetro que el código lee nace en Terraform, por tenant — uno
creado a mano rompe el próximo apply (`ParameterAlreadyExists`) y un tenant
nuevo no lo hereda. Mismo criterio que `test_lab_settings_terraform.py`.
"""
from __future__ import annotations

import re
from pathlib import Path

from src.platform.whatsapp.reengagement_frequency import CEILING_ENV

_TERRAFORM = Path(__file__).resolve().parents[3] / "infra" / "terraform" / "platform"


def _hcl(path: Path) -> str:
    return "\n".join(line.split("#", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())


def test_the_ceiling_the_code_reads_is_declared_as_a_tenant_parameter() -> None:
    text = _hcl(_TERRAFORM / "modules" / "lab-config" / "main.tf")

    assert re.search(rf"^\s*{CEILING_ENV}\s*=", text, re.M), (
        f"el código lee {CEILING_ENV} del entorno y lab-config no lo declara: "
        "agregarlo al mapa `params` (tenants.<t>.lab.remarketing_max_touches)"
    )


def test_the_tenant_variable_defaults_to_the_full_ladder_and_is_bounded() -> None:
    text = _hcl(_TERRAFORM / "variables.tf")

    assert re.search(r"remarketing_max_touches\s*=\s*optional\(number,\s*5\)", text)
    assert "t.lab.remarketing_max_touches" in text, (
        "falta la validación 0..5 de tenants.*.lab.remarketing_max_touches"
    )
