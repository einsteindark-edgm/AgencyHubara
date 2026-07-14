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


def test_main_repo_root_resuelve_worktrees():
    """Si forge corre desde un worktree (<repo>/.claude/worktrees/<x>), el repo
    a proteger es <repo> — no el worktree."""
    wt = Path("/Users/x/Proyectos/AgencyHubara/.claude/worktrees/rama-123")
    assert forge.main_repo_root(wt) == Path("/Users/x/Proyectos/AgencyHubara")
    normal = Path("/Users/x/Proyectos/AgencyHubara")
    assert forge.main_repo_root(normal) == normal


def test_apply_desde_worktree_rechaza_dest_dentro_del_repo_principal(tmp_path):
    """El caso real de la sesión: src = worktree, dest = carpeta dentro del
    checkout PRINCIPAL de hubara. El guard debe cubrir el repo principal, no
    solo el worktree."""
    main = tmp_path / "AgencyHubara"
    src = main / ".claude" / "worktrees" / "rama-x"
    src.mkdir(parents=True)
    dest = main / "AgencyAcme"  # dentro del repo productivo — prohibido
    with pytest.raises(forge.ForgeError, match="repo madre|productivo"):
        forge.run_apply(src=src, dest=dest, client_dir=tmp_path, manifest=forge.load_manifest())
