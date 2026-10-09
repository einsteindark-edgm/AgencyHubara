"""Ratchet del manifest contra el REPO REAL.

Cada regla de replacement debe matchear ≥1 archivo tracked del repo madre hoy.
Si una regla da 0, o bien el literal se movió/renombró (scope drift — actualizar
el manifest) o la regla sobra. Y al revés: cuando alguien agregue un literal de
cliente nuevo, el golden del clon lo atrapa como residual crítico.

Corre con `python3 -m pytest forge/tests -q` desde la raíz del repo.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

FORGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FORGE_DIR))

import forge  # noqa: E402

CLIENT = {
    "slug": "acme",
    "company": "Acme",
    "repo": "einsteindark-edgm/AgencyAcme",
    "aws": {"region": "us-east-1", "resource_prefix": "agencyacme", "ssm_prefix": "/acme"},
    "business": {"country": "CO", "currency": "COP", "domains": ["acme.example.com"]},
}


@pytest.fixture(scope="module")
def plan(tmp_path_factory):
    client_dir = tmp_path_factory.mktemp("client") / "acme"
    client_dir.mkdir()
    (client_dir / "client.yaml").write_text(yaml.safe_dump(CLIENT), encoding="utf-8")
    return forge.run_plan(forge.REPO, client_dir, forge.load_manifest())


def test_toda_regla_matchea_el_repo_real(plan):
    zero = {k: v for k, v in plan["replacement_files"].items() if v == 0}
    assert not zero, (
        f"reglas del manifest sin match en el repo real (scope drift): {sorted(zero)} — "
        "o el literal se movió (actualizar files/from) o la regla sobra"
    )


def test_deletes_existen_en_el_repo_real(plan):
    manifest = forge.load_manifest()
    missing = [d for d in manifest["deletes"] if d not in plan["would_delete"]]
    assert not missing, (
        f"paths de deletes que ya no existen en el repo (limpiar manifest): {missing}"
    )


def test_workspaces_del_overlay_existen(plan):
    for agent, (ws, required) in forge.overlay_agents(forge.load_manifest()).items():
        assert (forge.REPO / ws).is_dir(), f"workspace de {agent} movido: {ws}"
        for req in required:
            real = req.replace("catalog/", "hubara_catalog/")
            assert (forge.REPO / ws / real).exists(), f"{agent}: {real} ya no existe en el motor"


def test_el_paquete_del_clon_es_el_que_corre_la_tienda_madre():
    """Promover un paquete en Hubara (`tenants.hubara.lab.decisions_bundle`)
    sin avisarle a forge dejaría a los clones con la inteligencia vieja."""
    import re

    db = forge.load_manifest()["decision_bundles"]
    tfvars = (forge.REPO / "infra/terraform/platform/tenants.auto.tfvars").read_text()
    running = re.findall(r'^\s*decisions_bundle\s*=\s*"([^"]+)"', tfvars, re.M)
    assert running == [db["store"]], (
        f"la tienda madre corre {running} y forge clona {db['store']!r}: "
        "actualizar decision_bundles.store en forge/manifest.yaml"
    )
    bundles = forge.REPO / db["dir"]
    for b in {db["default"], db["store"]}:
        assert (bundles / b / "bundle.yaml").is_file(), f"no existe el paquete {b}"


def test_las_cifras_de_commerce_literals_son_las_de_la_tienda_madre():
    """`commerce_literals` es la política de la tienda madre tal como la citan
    sus workspaces (premortem 2026-10-09): si una tarifa cambia en el código y
    no acá, la voz del agente del clon heredaría la vieja."""
    import re

    sales = forge.REPO / "hubara_agency/src/plugins/chats/agent/sales/config"
    shipping = (sales / "shipping.py").read_text(encoding="utf-8")
    payments = (sales / "payments.py").read_text(encoding="utf-8")

    def amount(name: str) -> str:
        return forge.commerce_text(int(re.search(rf"{name}: int = ([\d_]+)", shipping).group(1).replace("_", "")))

    def text(src: str, name: str) -> str:
        return re.search(rf'{name}: str = "([^"]+)"', src).group(1)

    expected = {
        amount("local_cop"): "shipping_rate_local_cop",
        amount("national_cop"): "shipping_rate_national_cop",
        amount("cod_min_products_cop"): "cash_on_delivery_min_cop",
        text(payments, "link_surcharge_local"): "payment_link_surcharge_local",
        text(payments, "link_surcharge_other"): "payment_link_surcharge_other",
        text(shipping, "local_zone"): "shipping_local_zone",
        forge.INIT_REAL_DATA["3229041190"]: "payment_nequi_number",
    }
    manifest = forge.load_manifest()
    assert manifest["commerce_literals"] == expected
    voices = "".join(
        p.read_text(encoding="utf-8")
        for ws, _ in forge.overlay_agents(manifest).values()
        for p in sorted((forge.REPO / ws).rglob("*"))
        if p.is_file() and p.suffix in {".md", ".yaml"}
    )
    for literal, field in expected.items():
        if field != "payment_nequi_number":  # la llave se siembra como marca (INIT_REAL_DATA)
            assert literal in voices, f"{literal} ({field}) ya no está en los workspaces: la regla sobra"
