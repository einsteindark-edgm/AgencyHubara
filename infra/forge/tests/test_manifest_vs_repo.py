"""Ratchet del manifest contra el REPO REAL.

Cada regla de replacement debe matchear ≥1 archivo tracked del repo madre hoy.
Si una regla da 0, o bien el literal se movió/renombró (scope drift — actualizar
el manifest) o la regla sobra. Y al revés: cuando alguien agregue un literal de
cliente nuevo, el golden del clon lo atrapa como residual crítico.

Corre con `python3 -m pytest infra/forge/tests -q` desde la raíz del repo.
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
    ov = forge.load_manifest()["workspace_overlay"]
    for agent, ws in ov["agents"].items():
        assert (forge.REPO / ws).is_dir(), f"workspace de {agent} movido: {ws}"
        for req in ov["required"]:
            real = req.replace("catalog/", "hubara_catalog/")
            assert (forge.REPO / ws / real).exists(), f"{agent}: {real} ya no existe en el motor"
