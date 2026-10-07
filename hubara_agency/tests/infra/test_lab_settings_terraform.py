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


def test_production_and_the_lab_run_the_same_engine_profile() -> None:
    """El perfil que Terraform le da a producción por defecto
    (`tenants.<t>.lab.perception_profile` → `SALES_PERCEPTION_PROFILE`) es el
    del bot nuevo que mide el laboratorio (brazo B). Si divergen, el
    laboratorio aprueba un bot y producción enciende otro."""
    from src.plugins.chats.agent.sales.decisions.bots import DEFAULT_PROFILE, bot_for_arm

    variables = (_TERRAFORM / "platform" / "variables.tf").read_text(encoding="utf-8")
    match = re.search(r'perception_profile\s*=\s*optional\(string,\s*"([^"]+)"\)', variables)

    assert match, "variables.tf ya no declara el default de perception_profile"
    assert match.group(1) == DEFAULT_PROFILE == bot_for_arm("B").profile


def test_the_store_decision_bundle_is_configuration_born_in_terraform() -> None:
    """El paquete de decisión activo es de la tienda (PAQUETES_DE_DECISION.md
    §10.1): `tenants.<t>.lab.decisions_bundle` → SSM `SALES_DECISIONS_BUNDLE`
    → el resolutor. El default de Terraform tiene que existir en el repo: si
    no, producción arrancaría pidiendo un paquete que no está."""
    from src.plugins.chats.agent.sales.decisions import registry

    variables = (_TERRAFORM / "platform" / "variables.tf").read_text(encoding="utf-8")
    lab_config = (_TERRAFORM / "platform" / "modules" / "lab-config" / "main.tf").read_text(encoding="utf-8")
    default = re.search(r'decisions_bundle\s*=\s*optional\(string,\s*"([^"]+)"\)', variables)

    assert default, "variables.tf no declara tenants.<t>.lab.decisions_bundle"
    assert default.group(1) == registry.DEFAULT_BUNDLE
    assert (registry.BUNDLES_DIR / default.group(1) / "bundle.yaml").is_file()
    assert re.search(rf"^\s*{registry.BUNDLE_ENV}\s*=\s*var\.config\.decisions_bundle", lab_config, re.M), (
        "lab-config no materializa SALES_DECISIONS_BUNDLE"
    )


def _tenant_bundles(text: str) -> dict[str, str]:
    """`tenants.<t>.lab.decisions_bundle` de un tenants.auto.tfvars (los que
    lo fijan; los demás corren el default de la variable)."""
    out: dict[str, str] = {}
    for tenant, block in re.findall(r"^  ([\w-]+) = \{\n(.*?)^  \}", text, re.M | re.S):
        lab = re.search(r"^\s*lab\s*=\s*\{(.*?)^\s*\}", block, re.M | re.S)
        bundle = re.search(r'decisions_bundle\s*=\s*"([^"]+)"', lab.group(1)) if lab else None
        if bundle:
            out[tenant] = bundle.group(1)
    return out


def test_every_bundle_a_tenant_names_exists() -> None:
    """Promover un paquete = nombrarlo en el tenant (PAQUETES_DE_DECISION.md §9;
    Hubara corre `ventas-4` desde el 2026-10-07, #402). El que se nombra tiene que
    estar en el repo: si no, la API y el worker de ventas no arrancarían. Cuál
    corre cada tenant es dato del tfvars (volver atrás no toca esta prueba), y
    un clon de forge, sin bloque `lab`, corre el default de la variable."""
    from src.plugins.chats.agent.sales.decisions import registry

    bundles = _tenant_bundles((_TERRAFORM / "platform" / "tenants.auto.tfvars").read_text(encoding="utf-8"))

    for tenant, bundle in bundles.items():
        assert (registry.BUNDLES_DIR / bundle / "bundle.yaml").is_file(), f"{tenant}: el paquete {bundle!r} no existe"


def test_the_tenant_parser_reads_any_tenant_name() -> None:
    text = 'tenants = {\n  mi-tienda = {\n    api_url = "x"\n    lab = {\n      decisions_bundle = "ventas"\n    }\n  }\n  otra = {\n    api_url = "y"\n  }\n}\n'

    assert _tenant_bundles(text) == {"mi-tienda": "ventas"}


def test_terraform_accepts_the_same_bundle_ids_as_the_engine_and_the_lab() -> None:
    variables = (_TERRAFORM / "platform" / "variables.tf").read_text(encoding="utf-8")

    # El mismo patrón que el modelo del paquete y el brazo del laboratorio (hasta 40).
    assert 'regex("^[a-z][a-z0-9-]{0,39}$", t.lab.decisions_bundle)' in variables
