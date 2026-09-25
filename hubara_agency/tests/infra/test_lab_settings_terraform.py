"""Lo que el lado de producción del laboratorio lee del entorno nace en Terraform.

El botón "Nueva corrida" (API) y el exportador del banco (worker `sales_eval`)
leen `LAB_*` del `.env` que `render-env-from-ssm.sh` arma con SSM
`/hubara/<tenant>/`. Terraform es la fuente de verdad de esos parámetros: uno
que el código lee y Terraform no declara solo existe si alguien lo crea a mano
(y el apply siguiente choca con `ParameterAlreadyExists`), o no existe nunca.
Pasó con `LAB_INTERNAL_NUMBERS` (premortem de integración del laboratorio,
24-sep): sin él, el banco no excluía los números del equipo.

Se leen el código y el HCL como texto (mismo criterio que test_lab_box_iam.py).
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_READERS = [
    _REPO / "hubara_agency" / "src" / "plugins" / "chats" / "api" / "lab.py",
    *sorted((_REPO / "hubara_agency" / "src" / "plugins" / "chats" / "agent" / "sales_lab" / "launch").glob("*.py")),
]
_TERRAFORM = _REPO / "infra" / "terraform"
# Con valor por defecto en el código, a propósito (no hace falta por tenant):
_CODE_DEFAULTS = {
    "LAB_BENCH_SINCE": "corte del banco (plan §3.4): 2026-09-10; un tenant nuevo no tiene conversaciones anteriores",
}


def _read_by_code() -> set[str]:
    return {
        name
        for path in _READERS
        for name in re.findall(r"[\"'](LAB_[A-Z0-9_]+)[\"']", path.read_text(encoding="utf-8"))
    }


def _declared_in_terraform() -> set[str]:
    names: set[str] = set()
    for tf in _TERRAFORM.rglob("*.tf"):
        if ".terraform" in tf.parts:
            continue
        text = "\n".join(line.split("#", 1)[0] for line in tf.read_text(encoding="utf-8").splitlines())
        names |= set(re.findall(r"^\s*(LAB_[A-Z0-9_]+)\s*=", text, re.M))  # clave del mapa de params (lab-config)
        names |= set(re.findall(r"/hubara/\$\{[^}]+\}/(LAB_[A-Z0-9_]+)", text))  # nombre del parámetro (compute)
    return names


def test_the_guard_sees_what_the_launcher_reads() -> None:
    assert {"LAB_MAX_USD_PER_RUN", "LAB_MAX_USD_PER_MONTH"} <= _read_by_code() & _declared_in_terraform()


def test_every_lab_setting_that_production_reads_is_declared_in_terraform() -> None:
    missing = _read_by_code() - _declared_in_terraform() - set(_CODE_DEFAULTS)

    assert not missing, (
        f"el laboratorio lee {sorted(missing)} del entorno y Terraform no lo declara: "
        "agregarlo por tenant en infra/terraform/platform/modules/lab-config (tenants.<t>.lab)"
    )
