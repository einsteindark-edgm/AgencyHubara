"""Guards anti-hubara: NINGUNA ejecución de forge/steps puede apuntar al
proyecto productivo. Estos tests son el contrato de esa garantía."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

FORGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FORGE_DIR))

import forge  # noqa: E402


def _client(**over):
    base = {
        "slug": "acme",
        "company": "Acme",
        "repo": "einsteindark-edgm/AgencyAcme",
        "aws": {"resource_prefix": "agencyacme", "ssm_prefix": "/acme"},
        "business": {},
    }
    base.update(over)
    return base


def test_slug_hubara_rechazado():
    with pytest.raises(forge.ForgeError, match="hubara"):
        forge.render_vars(_client(slug="hubara"))


def test_prefijo_de_recursos_de_hubara_rechazado():
    with pytest.raises(forge.ForgeError, match="agencyhubara"):
        forge.render_vars(_client(aws={"resource_prefix": "agencyhubara", "ssm_prefix": "/acme"}))


def test_ssm_prefix_de_hubara_rechazado():
    with pytest.raises(forge.ForgeError, match="/hubara"):
        forge.render_vars(_client(aws={"resource_prefix": "agencyacme", "ssm_prefix": "/hubara"}))


def test_repo_de_hubara_rechazado():
    with pytest.raises(forge.ForgeError, match="AgencyHubara"):
        forge.render_vars(_client(repo="einsteindark-edgm/AgencyHubara"))


def test_apply_rechaza_dest_dentro_del_repo_madre(tmp_path):
    """Forjar DENTRO del repo madre mezclaría el clon con hubara — prohibido."""
    inner = forge.REPO / "algo" / "clon"
    with pytest.raises(forge.ForgeError, match="repo madre"):
        forge.run_apply(
            src=forge.REPO,
            dest=inner,
            client_dir=tmp_path,  # ni llega a leerse: el guard va primero
            manifest=forge.load_manifest(),
        )
